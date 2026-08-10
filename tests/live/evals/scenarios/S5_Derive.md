---
id: S5_Derive
tier: pipeline
fixture: the S4 artifact P4-Gimbal (verified BY URN) OPENED by the orchestrator as the derive
  SOURCE and left open, THEN a fresh empty design staged as the ACTIVE document (orchestrator:
  doc_new AFTER the source open, so the empty doc is not consumed). The agent derives FROM the
  already-open P4-Gimbal into the active document; P4-Gimbal's cloud version must remain
  untouched. Pre-opening by URN is REQUIRED: the name "P4-Gimbal" is not unique in the data model
  (per-run subfolders + legacy chains), so a by-name search picks the wrong lineage. Missing
  fixture = ask - never create a project.
budget:
  max_tool_calls: 72
  max_tokens: 76000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S5 - Derive: the machining-prep model

Goal-shaped. Pull ONE part out of the finished gyroscope as a DERIVE - a one-way linked copy -
and build the machining-prep layer on top of it: patch surfaces, offset surfaces, boundary
sketches, and a part-space joint origin. Produces the MODEL file the CAM chain consumes. Grades
the derive mechanism's semantics and surface-modeling on derived geometry.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is a fresh empty document. The source design "P4-Gimbal" is ALREADY OPEN in
this session (it was opened for you as the derive source - it is the other open document; do NOT
search the data panel for it, and do not open a second copy, because the name is not unique).
Work IN the active design and do all modeling there; derive FROM the already-open P4-Gimbal;
never modify or save the source, and always return to the active design. Missing save-target
project = STOP, report BLOCKED.

Cold start: sys_capability_map, then workspace_orient.

GOAL - a machining-prep MODEL file for the gyroscope's OUTER RING (the middle ring of the stack,
between frame and inner ring):

- INSERT A DERIVE of ONLY the outer ring component from P4-Gimbal into the active document - a
  one-way linked copy: it updates FROM the source; nothing you model here travels back. Nothing
  else from the source may land: the machining model is the ring ALONE.
- Confirm what you received: the derived geometry is real, linked, CURRENT against its source,
  and exactly the one component (fresh reads, not assumption).
- Build the machining-prep layer ON the derived body (one element of it: a DATUM PLANE that
  sits PART-WAY ALONG one of the ring's circular edges - anchored to the edge itself at a
  fraction of its length, not at a coordinate you typed - so it rides the edge if the ring
  resizes; report the fraction you chose and the plane's read-back position):
  - DETAIL FEATURES on the derived body itself: at least EXTERNAL FILLETS on outer edges you
    choose (report the edges and the radius) - a derive is locally editable, and the point is
    that these edits live HERE while the source stays authoritative,
  - PATCH surfaces closing each radial CROSS-HOLE opening (the small side holes through the
    ring wall) - a toolpath should see those closed. The central bore stays OPEN - do NOT
    patch it. These openings are hard: if a patch refuses, read its error - it names what the
    boundary needs,
  - OFFSET surfaces from at least two faces - one at ZERO offset and one at a nonzero offset
    you choose (report which faces and offsets),
  - at least one BOUNDARY SKETCH projecting/outlining machining-relevant geometry,
  - a JOINT ORIGIN at the center of the model's bounding box, axes oriented to the machining
    direction you choose.

Finally save the document as P5-RingModel into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the derive is REAL, LINKED, and SCOPED: a fresh reference read shows a derive-kind reference
  to P4-Gimbal, current (not stale) - and a fresh tree read shows EXACTLY the outer ring landed
  (the one derived component and its body count), nothing else from the source.
- parameters: report how many parameters imported with the derive (a low/zero count is a known
  platform behavior - report the read honestly, it is diagnostic, not pass/fail).
- the detail features exist ON the derived body: fresh reads show the fillet feature (its edge
  count and the radius you declared) and the body's volume changed from your pre-fillet read.
- the along-edge datum plane exists, anchored to the edge at your stated fraction (a fresh
  construction read reports it; a plane placed at a bare coordinate fails the anchored clause).
- the prep layer exists: a patch surface per cross-hole opening (report the patch body count
  and which opening each caps; a fresh read shows the central bore still OPEN), offset
  surfaces (>= 2, with the zero and nonzero offsets you declared), the boundary sketch, and
  the joint origin at the measured bbox center (report the center you measured and the
  origin's read-back position - they must match).
- one-way proof: your prep work - INCLUDING the local edits on the derived body - did NOT touch
  the source: a fresh cloud read shows P4-Gimbal still at the version you found it.
- doc_get -> saved as "P5-RingModel", real URN, version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}.

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

- WHAT THIS MEASURES: doc_insert_derive with COMPONENT SCOPING on the wire, derive-vs-xref
  comprehension (the description must carry it), local detail edits on a derived body (the
  one-way semantics made concrete), the surface family (patch/offset at zero AND nonzero - the
  zero-offset copy is a real machining-prep idiom), sketch projection on derived geometry, and
  joint_create_origin(bbox_center) with orientation.
- The tool REQUIRES the source document open (its precondition names doc_open). The orchestrator
  PRE-OPENS the source by URN: a blind by-name search picks the wrong
  lineage because "P4-Gimbal" is not unique (per-run subfolders + legacy Rig-Sources chains) -
  it derives a stale Rig-Sources P4 (OD r126) instead of the pipeline's P4 (OD r60).
  Source IDENTITY is not a graded skill; the derive mechanism + prep layer are. Pre-opening
  removes the ambiguity while keeping every graded element.
- Freshness grading is FRESH-STATE ONLY here: the staleness + refresh-refusal story (stale
  visible in the derive-kind reference row; in-place refresh refused by the platform; fallback =
  delete + re-derive) is live-verified at the TOOL level and deliberately NOT staged in the
  pipeline - staging it would version-bump the immutable P4 artifact. Deviation from the
  authoring spec, by design.
- Parameter import is DIAGNOSTIC: the include-parameter flags are a live-measured platform
  NO-OP (0 of 17 imported with both true); the tool surfaces parameter_warning honestly. Grade
  the honest report, not the count.
- SCOPING is graded: the tool takes source_components, so a whole-source landing (multiple
  components in the tree read) is a task-comprehension FAIL, not a tool limit.
- CROSS-HOLE PATCHES are the graded surface work (rule: cap the side holes, never the
  central bore - the bore is the fixturing/datum surface). The openings are the degenerate
  tangent-saddle class: a single-seed patch fails on them, and the working technique is an
  explicit boundary from the opening's two half-edges (live-verified recipe; surface_patch's
  error names it on failure). Grade the honest struggle-and-recover path; a silently skipped
  opening or a patched-over bore is a FAIL of this element.
- Staging: verify P4 present BY URN; doc_open the source P4 by URN (force_api_open, leave open);
  doc_new for the fresh empty active AFTER; verify twice; run the block. Budget:
  measured + 25% (72 calls incl. a surface_offset chaining recovery); the
  cross-hole patch work replaces the single bore patch - recalibrate from the next blind run's
  measured count.
