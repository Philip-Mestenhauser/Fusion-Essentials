---
id: S2c_Regeneration
tier: pipeline
fixture: P2-Gimbal (the S2b artifact - pins, bores, seats, zero-interference verified) OPENED as
  the active document by the orchestrator BY URN, active hub PINNED first. READ-AND-RESTORE
  stage - the agent changes parameters but never saves; the cloud artifact must remain at the
  version staged. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 43
  max_tokens: 32000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# S2c - Regeneration: the parametric model is real, or it is scenery

Goal-shaped micro-stage. The chain claims the gimbal is parametric - one driving diameter
propagating through every part. This stage PROVES it on the BUILT SOLIDS: bump the driver,
recompute, and require the geometry to actually follow, the timeline to stay healthy, and the
zero-interference clearance state to survive at the new scale. A model whose sketches carry
expressions but whose solids do not follow is scenery, not a parametric model - this stage
exists to fail it. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: the saved document "P2-Gimbal" - the gyroscope with its
pivot pins, bores, and bearing seats, verified interference-clean (contact only where pins and
shaft meet their bores and seats). Work IN the active design; do not create, open, or switch
documents, and do NOT SAVE at any point - this stage proves a property and leaves the document
exactly as found (your restore is verified by reads, not by a save).

Cold start: sys_capability_map, then workspace_orient.

GOAL - PROVE THE MODEL REGENERATES:

1. Find the DRIVING diameter parameter (the one the chain built everything from - read the
   user parameters and pick it by name and comment; report your choice and why).
2. Record the BASELINE: the driver's value, a fresh health read, a fresh interference check,
   and fresh geometry reads of at least THREE solids at different depths of the chain (e.g. a
   ring's outer radius, a pin's radius or position, the rotor's extent) - values with units.
3. BUMP the driver by 10-15% via param_set. Recompute if the design does not do so itself.
4. Read the SAME three geometry values fresh. Each must have MOVED consistently with the bump -
   a value that did not change names a part whose solids are not actually driven by the
   parameter; report it as the defect it is.
5. Health read: the timeline must recompute with ZERO errors at the new scale.
6. Interference check at the new scale: contact ONLY where pins/shaft meet bores/seats - the
   clearance architecture must survive scaling, not just exist at one lucky size.
7. RESTORE the driver to its exact baseline value. Fresh reads: the three geometry values match
   the baseline again, health is clean, interference matches the baseline state.

Do NOT fix any defect you find - this stage MEASURES regeneration; a failure here is upstream
work product, and your honest report of exactly what broke and where is the deliverable.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the driver was identified and reported (name, value, why you chose it).
- PROPAGATION AT THE SOLID LEVEL: all three tracked geometry reads moved under the bump,
  consistent in direction and proportion with the change (report before/after per value).
- HEALTH UNDER REGENERATION: zero timeline errors after the bump (fresh health read quoted).
- CLEARANCE SURVIVES SCALE: the post-bump interference check reports contact only at the
  pin/bore and shaft/seat interfaces (report the checker's actual output).
- CLEAN RESTORE: after restoring the driver, the three geometry reads match their baselines,
  health is clean, and the document was never saved (report the doc's saved/modified state).

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read, with units>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: whether the chain's parametric claim holds at the SOLID level. Sketch
  expressions are necessary but not sufficient - features built from baked coordinates (gear
  teeth computed by a generator and pasted as frozen output are the canonical
  example) pass every static read and fail exactly
  here. Regeneration is the cheapest un-fakeable parametric proof: one param_set, and either
  the model follows or it does not.
- WHY A SEPARATE MICRO-STAGE: the build stages (S2a/S2b) run near the harness's ~60-min cap;
  regeneration grading is ~40 calls of pure reads + two param_sets and would risk cap-kills
  bolted onto either. Standalone, it also isolates blame: an S2c FAIL names upstream work
  product without contaminating a build stage's verdict.
- A FAIL here is a GOOD run when honestly reported - the stage exists to catch baked geometry
  and brittle parametrics; grade the honesty and precision of the defect report, not just the
  pass bit. The executor must NOT repair anything (repairs belong to the owning stage).
- NEVER-SAVE is load-bearing: the fixture stays at its staged version; verify by a fresh cloud
  read AFTER the run (orchestrator) that P2-Gimbal's version is unchanged. If an executor
  saved, the run is INVALID regardless of verdict - restage from the prior version.
- Staging: doc_open the S2b artifact BY URN (force_api_open), confirm active; run the block.
- Chain placement: after S2b, before S3 (S3 consumes the same P2 artifact; S2c leaves it
  untouched). Budget PROVISIONAL 45 calls / 32k tokens - recalibrate to measured + 25% once a
  measured run lands.
