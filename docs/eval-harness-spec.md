# Spec: Agent Eval Harness for the Fusion-Essentials MCP Server

Status: PROPOSAL - not implemented. This document is a self-contained brief for the
agent/developer who builds it. It is intentionally not part of the MCP-server
contribution itself; treat it as an input, not shipped code.

## 1. Purpose and the gap it closes

The server has 2,500+ unit tests (mocked `adsk`, run in ~15 s) plus lints for naming,
write-status, ASCII wire strings, helper duplication, and doc freshness. All of that
proves the HANDLERS are correct. None of it measures the thing the server exists for:
**can an LLM agent actually drive the tool surface to complete CAD/CAM tasks** - with
few blind calls, correct verification habits, and bounded token cost.

That property regresses silently today. A description edit that drops a load-bearing
gotcha, a new tool that overlaps an old one, or a payload that grows chatty will pass
every existing test. The eval harness is the missing top of the test pyramid: scripted,
repeatable, agent-in-the-loop scenarios scored against ground truth read back from the
live server.

## 2. Prior art (verified 2026-07-06, chrome-devtools-mcp v1.5.0)

Google's chrome-devtools-mcp - the closest published analog (an MCP server driving a
stateful GUI app) - ships an eval harness worth copying in shape:

- `scripts/eval_gemini.ts` runs task-level evals against a live browser via the real
  MCP server; ~19 scenario files under `scripts/eval_scenarios/`.
- Scenarios are task prompts + expected outcomes, run by an actual model (Gemini),
  not replayed tool calls - so they catch description/affordance regressions, not
  just handler bugs.
- Run on demand (`npm run eval`), not in unit CI: evals are slower, cost tokens, and
  need a live app instance. Same split applies here.
- They also track per-tool token metrics (`npm run count-tokens`,
  `tool_call_metrics.json`) so payload growth is observable over time.

## 3. Constraints specific to this server

1. **Live Fusion required.** No headless mode exists. The harness runs on a developer
   machine with Fusion running and the add-in loaded (server at
   `http://127.0.0.1:27182/mcp`, health probe at `/health`). CI execution is out of
   scope; the harness is a developer tool like the live-validation step in
   `tools/CLAUDE.md`.
2. **Windows first.** Match the repo's documented platform support; macOS is untested
   for `sys_execute_script` and can stay out of scope.
3. **Async platform behavior.** `doc_open`/`doc_save` return before completion;
   `cam_generate` is fire-and-poll. Scenario scoring must re-read state (the server's
   own guidance) rather than trusting call results - which conveniently is also what
   we want to grade the agent on.
4. **Cloud state.** Some tools (drawing_create, data_*) need documents saved to the
   cloud hub. Smoke scenarios must avoid cloud dependencies; a later tier can accept
   them behind a flag.
5. **Cost.** Each scenario is a real agent session. Budget per-scenario token caps and
   keep the smoke tier under ~10 minutes wall clock for the default 3 scenarios.
6. **Safety.** Scenarios create their own scratch documents/components and never touch
   pre-existing user documents. `sys_execute_script` stays disabled during evals - the
   whole point is grading the typed surface.

## 4. Proposed architecture

Three parts, deliberately minimal:

### 4.1 Scenario files (`evals/scenarios/*.md` or `.yaml`)

One scenario = fixture + task + postconditions + budgets:

- `id`, `tier` (smoke | full | cloud)
- `fixture`: how to reach the starting state. Prefer "fresh empty design" (a `doc_new`
  performed by the RUNNER, not the agent). Richer fixtures may be built by a scripted
  setup sequence of direct MCP calls (not agent turns), so fixtures are deterministic.
- `task`: the natural-language prompt handed to the agent, written like a real user
  ("make a 40x20x10 mm block with a 6 mm through-hole centered on the top face").
- `postconditions`: a list of CHECKS the runner executes as direct MCP read calls
  after the agent finishes - the session-level analog of `tools/_assert.py`'s
  verify-the-effect kinds. Each check names a read tool, its arguments, a JSONPath-ish
  extractor, and an expected value/tolerance. Example:
  - `model_inspect` -> exactly 1 body; volume within 2% of 7,433 mm^3
  - `find_geometry(kind=face, ...)` -> a cylindrical face of radius 3 mm exists
  - `assembly_probe` -> occurrence X grounded
- `budgets`: max agent turns, max tool calls, max total tokens.
- `expected_refusals` (optional, doctrine scenarios only): guard refusals the scenario
  deliberately induces (e.g. the `expect_document` mid-task switch). These are excluded
  from the wasted-call diagnostic - the guard firing is the system working.

Scoring philosophy: **guard the outcome, observe the process.** Scenarios never score
WHICH tools were used or in what order - Fusion is broad and many paths are valid, and
a process-scored eval calcifies one workflow and gets gamed by description tuning. The
pass gate is outcomes only (Section 4.3); everything about the path taken is recorded
as diagnostics for a human reading a regression, never as pass/fail. The few scenarios
that test a doctrine itself are labeled as such and kept rare.

### 4.2 Runner (`evals/run.py`)

Plain Python 3, stdlib + the developer's chosen agent CLI. Suite start (once):

- Reload the add-in (`sys_reload_addin`), wait for `/health`, and only THEN record the
  server git SHA into the results. Without the reload, the add-in may be running code
  from before a branch switch and the SHA tag is fiction.
- Startup sweep: close any unsaved document whose name matches the `EVAL_` prefix
  (leftovers from a crashed run). Never `close_all` - the developer's real documents
  are open.

Loop per scenario (default 3 runs each; single runs of a stochastic agent are noise,
and the A/B use case in 4.3 needs pass-rates and medians, not anecdotes):

1. Probe `/health`; verify `server` is the Fusion-Essentials name (reuse the
   `verify_ownership` logic in `server/mcp_server.py`).
2. Execute fixture setup via direct MCP `tools/call` requests (simple JSON-RPC over
   HTTP POST; no SDK needed - see `tests/` for request shapes). Fixture documents are
   named `EVAL_<scenario>_<timestamp>`.
3. Launch the agent against the task with an ISOLATED config (a sterile
   `CLAUDE_CONFIG_DIR`): the developer's own `~/.claude` rules would otherwise ride
   into every run and shift baselines whenever they edit their personal kit. Record a
   hash of the effective config in the result row. Recommended first target: Claude
   Code headless (`claude -p "<task>" --mcp-config <cfg> --allowedTools
   "mcp__fusion-essentials__*" --output-format stream-json`), which yields a
   machine-readable transcript including every tool call and token usage. Keep the
   agent adapter behind a small interface so a second CLI can be added later. The
   agent never sees the scenario's postconditions or budgets - it gets the natural
   user ask, cold.
4. Run postcondition checks via direct MCP reads, and check the agent's FINAL REPORT
   for truthfulness (see 4.3).
5. Teardown: close the scratch document without saving (`doc_close` by name), verify
   closed.
6. Emit one JSON record per run (see 4.3) and a human summary table aggregated per
   scenario.

### 4.3 Scoring and metrics

The pass gate is outcomes only, three parts:

- `postconditions_pass`: the runner's direct reads match the scenario's expected state
- `report_truthful`: every checkable claim in the agent's final message matches
  machine state as the runner reads it. This is how the verify-after-write doctrine is
  graded WITHOUT prescribing process: an agent that skips verification and claims
  "toolpaths generated" when they are not fails here, on outcome. (This replaces any
  read-after-write heuristic - do not score process.)
- `within_budget`: turns, tool calls, tokens under the scenario caps

Recorded as DIAGNOSTICS (visible in reports, never gating):

- `turns`, `tool_calls`, `tokens_in/out` (from the agent transcript)
- `wasted_calls`: calls returning `isError` or refusals, minus the scenario's
  `expected_refusals`
- `stall_chains`: runs of consecutive failed/retried calls with no state progress -
  the off-the-rails / debugging-spiral signal
- tool-usage sequence, for a human diagnosing WHY a scenario got slow or expensive
- raw transcript path for post-mortems

Store results as JSONL under `evals/results/` (gitignored - transcripts contain the
developer's private hub/project identifiers and are never committed or shared), one
row per run, keyed by scenario id, model, config hash, and the post-reload server git
SHA. A/B comparison across branches diffs per-scenario pass-rates and median
diagnostics across the run set, not single runs.

Scenario-set hygiene: hold one or two scenarios out of the description-tuning loop
(never consulted while editing tool text), and retire any scenario that always passes
- a gauge that never rejects has stopped gauging. Prefer many small outcome-scored
scenarios across tool families over a few deep process checks.

### 4.4 What the harness must NOT do

- No mocking. If Fusion is not running, refuse loudly with setup instructions.
- No screenshots-based grading in v1 (subjective; the structured reads are the
  ground truth by design).
- No modification of server code. If a scenario needs a capability the server lacks,
  that is a FINDING to report, not a hook to add.

## 5. Initial scenario set (smoke tier, build these first)

1. **orient-and-measure**: fixture = a scripted two-body design built from KNOWN
   dimensions, so ground truth is a constant written in the scenario file (never
   measured with the same instrument under test). Task: "How far apart are the two
   blocks and what are their volumes? Answer in mm." (pin the units in the prompt or
   correct cm answers false-fail). Outcome-scored only: no writes occurred, and the
   final text contains the expected numbers within tolerance - which tools it used to
   get there is diagnostics, not score.
2. **sketch-extrude-verify**: fixture = empty design. Task: block + centered through
   hole (dimensions in mm). Postconditions: body count, volume tolerance, cylindrical
   face radius - all derivable as constants from the task's own dimensions.
3. **cam-fire-and-poll** (only if a CAM-capable fixture proves scriptable; otherwise
   defer to full tier): create setup + one operation, generate. Outcome-scored via
   report truthfulness: the runner reads real toolpath state and compares it to what
   the agent CLAIMED - an agent that assumed instead of polling reports generated
   toolpaths that do not exist, and fails on that mismatch.

Full tier later: joints/kinematics (joint_at_geometry off find_geometry handles),
`expect_document` discipline under a mid-task document switch (the runner switches the
active doc between agent turns via a direct call - the agent should be refused and
recover), destructive-guard behavior (`data_delete_folder` confirm_name), drawing +
export with `verify_written` evidence.

## 6. Definition of done for v1

- `evals/README.md` with setup (enable server, model config, cost expectations, and
  the warning that transcripts contain private hub/project identifiers).
- Runner (`--tier smoke --runs 3` default) + 3 smoke scenarios passing against live
  Fusion on Windows with at least one model, results JSONL emitted with pass-rates
  and median diagnostics.
- One intentional regression demonstrated: break a tool description (e.g. delete the
  fire-and-poll sentence from `cam_generate`), show the relevant scenario degrade
  (pass-rate or wasted-call diagnostic across the run set), restore. This proves the
  harness bites - the same "prove a test actually bites" rule as `tests/CLAUDE.md`.
- Total new code small enough to review in one sitting (~500 lines target, excluding
  scenarios).

## 7. Decisions already made (do not re-litigate) + open questions

Decided:
- Ground truth: constants-by-construction in the scenario file wherever the fixture's
  known dimensions make a value derivable; runner-side reads only for state that is
  not derivable (handles, generated names). Never measure the reference standard with
  the instrument under test.
- Teardown: `EVAL_` name prefix on all fixture docs; startup sweep closes unsaved
  prefix-matching docs only. Never `close_all`.
- Runs: 3 per scenario by default; scoring and A/B on pass-rates and medians.
- Config: isolated agent config per run, config hash recorded.
- Cost: suite-level token cap; exceeding it refuses to run without an explicit flag.

Open:
1. Agent transcript formats differ per CLI; parse Claude Code stream-json tolerantly
   (ignore unknown event types) rather than pinning a version - revisit if it breaks.
2. How to bound `report_truthful` checking: v1 keeps it dumb (expected numbers/claims
   listed per scenario, string/tolerance match against the final message), not an
   LLM judge. Revisit only if dumb matching proves too brittle.

## 8. Repo conventions the implementer must follow

- Pure ASCII in anything that could cross the wire or get committed near the server
  (see root `CLAUDE.md`); this spec and the harness live outside `commands/mcpServer/`
  so the wire lints do not apply, but match the style anyway.
- Scenarios must create their own scratch state and never rely on or mutate the
  developer's real documents (see "Destructive / outward actions" in
  `commands/mcpServer/tools/CLAUDE.md`).
- The harness is a developer tool: `py -3 evals/run.py --tier smoke` should be the
  whole interface.
