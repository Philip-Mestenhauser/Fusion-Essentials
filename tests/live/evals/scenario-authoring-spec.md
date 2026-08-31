# Scenario authoring spec - binding for scenarios/*.md

The scenario docs in [scenarios/](scenarios/) are the single source of truth for WHAT each stage
builds and grades; this spec governs their SHAPE. run_eval.py extracts the AGENT PROMPT block
verbatim and launches it blind; the orchestrator stages the fixture first and grades afterward by
re-issuing every postcondition read itself. Edit a scenario and this contract together or the
runner/grader stops matching the docs.

## Frontmatter contract (per scenario)

- `id` / `tier: pipeline`
- `fixture`: the exact staging steps, BY URN (same-name lineages exist in the data model; staging
  or grading by name targets the wrong one). Scoped to eval-created docs only - NEVER close_all.
  Missing fixture = the executor reports BLOCKED; a scenario never creates a project.
- `budget`: `max_tool_calls` = the last measured run + 25%. The RUNNER'S AUDIT count governs; the
  executor's TOOL_CALLS line is graded for honesty, not arithmetic. The runner derives its
  --max-turns backstop from this number (2x, floor 120), so an understated budget severs runs.
  `max_tokens` = the last measured run's OUTPUT tokens + 25% - the runner scores it the same way
  (audit.json's `within_token_budget`), so a postcondition change that adds reads re-pins BOTH.
- `skill` (optional): a directory name under `.claude/skills/`. The runner appends that skill's
  body - its `SKILL.md` below the frontmatter - after the task block. The executor cannot invoke a
  skill (the `Skill` tool is denied and its cwd holds no repo), so the practice travels in the
  prompt or not at all. Declaring one CHANGES THE EXPERIMENT: without a skill the scenario measures
  what the WIRE alone teaches an agent; with one it measures the wire plus that practice, so runs of
  the two do not compare and BOTH budgets have to be re-measured from a run under the skill.
  `audit.json` records which skill a run carried. A named skill that is missing on disk STOPS the
  run rather than quietly running without it.
- `substitutions`: `{{RUN_FOLDER}}` is the one sanctioned token - the runner substitutes its
  per-invocation cloud subfolder tag so same-name artifact collisions across runs are impossible.
- `perturbations` / `expected_refusals`: name them, or `none`.

## Prompt-block mechanics (every scenario)

- The block is handed VERBATIM except for the runner's three fixed additions - the declared
  {{placeholders}} substituted, the `skill:` body appended when one is declared, and the
  MCP-CONNECTION-LOST paragraph appended last (README's "How to run one"). The task block always
  stays FIRST, so nothing appended can be read as amending it. Write it for a blind, context-isolated executor: no repo access,
  no harness utilities (the runner hard-denies them), MCP tools preloaded, so no ToolSearch step.
- No AskUserQuestion, no human click: geometry picks go through find_geometry; ambiguity = report
  BLOCKED, never improvise a confirm.
- Cold start: sys_capability_map, then workspace_orient, then drill.
- Verify with numbers AND eyes: screenshots at build milestones read against fresh numeric reads
  (a solver-symmetry break shows only in the render, not the numeric read).
- Postconditions: each names the READ that proves it; the read always wins over intent; claims
  that outran reads = REPORT_TRUTHFUL FAIL.
- REPORT block, exactly: VERDICT / POSTCONDITIONS (per-line PASS-FAIL + actual value) /
  REPORT_TRUTHFUL / VISUAL_CHECK / TOOL_CALLS / BREAKDOWN / SURFACED
  (TOOL:|WIRE:|CAPABILITY|EVAL|CLEAN) / NOTES.
- Values are quoted WITH units; a cross-tool numeric comparison states both units.

## Chain + grading rules

- Chain order: S1 -> S2a -> S2b -> S2c -> S3 -> S4 -> S5 -> S6 -> S7 -> S8 -> S9. Artifact
  handoff (P1..P7) is a GRADED postcondition in the producer, and hand-forward is BY URN. P6-Vise
  and P7-Template are mutable by design (the x-ref staleness proof edits them); the rest are
  immutable once graded.
- The orchestrator grades by re-issuing every postcondition read; historical claims (propagation,
  articulation) verify against SERVER-RETURNED values in the transcript, never the report alone.
- Records: the runner writes results/Eval-<date>/run_<scenario>_<NN>/ (prompt.txt as sent,
  transcript.jsonl, report.txt, audit.json); the orchestrator adds grade.md per run and the
  batch's INDEX.md.
- Stage size: executors run under a hard wall-clock cap (~60 min), so a scenario stays well under
  ~150 calls; when grading a property would bolt ~40+ read calls onto a build stage, split it into
  its own micro-stage (S2c is the shape) - that also isolates blame to upstream work product.
- A FAIL honestly reported and precisely located is a good run; the eval exists to surface tool,
  wire, and capability gaps (the SURFACED line), not to flatter the surface.

## Open-endedness rules (binding)

- **Goals name OUTCOMES, never construction.** A prompt states what the product must BE and DO;
  it never prescribes which sketches to draw, which features to use, where a profile lives, or a
  radius chain. The executor's construction choices are the thing under test. The tell that a
  scenario has gone prescriptive: geometry whose only consumer is a postcondition. A 12-run
  campaign showed prescribed sketch features (pin circles, hub profiles, a shaft profile) that no
  later stage consumed - the next stage redrew its own.
- **Postconditions are FUNCTIONAL INVARIANTS**, few and strong: what must engage, what must
  clear, what must propagate, what must read back - each naming the fresh read that proves it.
  Arbitrary count floors ("at least 8 dimensions across 4 components") are a smell; prefer "every
  driving dimension of skeleton-anchored geometry carries an expression, spot-verified".
- **The ENGAGEMENT CONTRACT replaces blanket contact laws.** Parts ENGAGE where the mechanism
  needs it (each engagement NAMED, proven by a contact/coincident-face read or a ~0
  measure_between) and CLEAR where they move (proven by the interference check: zero overlapping
  pairs). A blanket "nothing touches" law is forbidden - it manufactures floating products
  (a carrier that never reaches its post) that pass every check. Penetration is always a defect;
  contact is graded against the declared engagement list.
- **PRODUCT-BAR GRADE.** For any physical deliverable the orchestrator judges the terminal
  artifact as a product, with eyes (screenshots / orthographic views) against a calibrated bar,
  and re-issues its own reads per postcondition CLASS it upholds - including at least one
  sketch-level read when sketch geometry is graded; census reads alone are insufficient grading.
  A saved artifact holding a physically impossible pose, a part buried in another, or an
  operation that cannot compute is a product FAIL even when honestly disclosed in the report.
- **QUARANTINE THE SAVE.** A scenario that deliberately forces an infeasibility disclosure must
  not ship the broken state as the terminal cloud artifact: the executor discloses, then restores
  a physically valid state before the save (the disclosure lives in the report). The saved
  artifact is what a cloned repo's user opens - it must be a product.

## Volumetric audit clause (required for physical-deliverable scenarios)

A scenario whose deliverable is solid geometry includes, in its verbatim prompt, the volumetric
audit habit (per-body volume reads + a body census at build milestones and once as a final
audit) and a one-connected-solid postcondition - the orphan/multi-lump defect class is invisible
to interference reads and screenshots, and two builds shipped floating pieces before this clause
existed. The grader re-issues its own volumetric inventory when upholding a PASS.

## tool_coverage.md

[tool_coverage.md](tool_coverage.md) maps scenarios to the tools they exercise; regenerate it from
the scenario docs whenever one changes coverage.
