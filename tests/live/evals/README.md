# Evals: point an agent at a scenario and see if the tool surface is drivable

Each file in `scenarios/` is a **self-executing eval**: hand one to a capable agent connected to a
live Fusion MCP session and it runs the whole test itself. The scenario file IS the instruction set -
it tells the agent how to execute and how to grade. Scenarios grade the OUTCOME (did the right thing
get built, and did the agent report it truthfully), never the path taken.

This is the top of the test pyramid: the mock unit suite proves handler LOGIC, `tool_verify.py`
proves every tool FUNCTIONS when called correctly, and these prove an agent holding only the wire
DESCRIPTIONS can actually drive the surface. Description and schema defects surface here the way a
user hits them.

## How to run one

Every scenario embeds an `AGENT PROMPT (verbatim)` block. Stage the fixture named in the
frontmatter, then run that block through `tests/live/evals/run_eval.py`. The runner spawns a
context-isolated headless executor: empty scratch cwd, sterile config, only the fusion-essentials
MCP server on its wire, source tools hard-denied.

**The block reaches the executor BYTE-IDENTICAL.** Compose nothing around it, so every run of a
scenario is the same experiment and runs compare cleanly. The runner makes exactly three additions,
identically on every scenario:

1. It substitutes the declared `{{placeholders}}`. The frontmatter's `{{RUN_FOLDER}}` is the
   per-invocation cloud subfolder tag; it carries SECONDS and the scenario stem, so two runs can
   never share one.
2. When the frontmatter declares `skill: <name>`, it APPENDS that skill's body (the `SKILL.md`
   below its frontmatter, from `.claude/skills/`) under a fixed header. The executor cannot invoke
   a skill - the `Skill` tool is denied and its cwd holds no repo - so the practice is carried in
   the prompt or not at all. **This changes the experiment, and deliberately:** a scenario with no
   skill measures what the WIRE alone teaches an agent; a scenario with one measures the wire plus
   that practice. Runs of the two do not compare, so `audit.json` records which skill a run carried
   and a scenario's budget has to be re-measured when its skill changes.
3. It APPENDS one fixed paragraph, the MCP-connection-lost rule (`CONNECTION_LOST` in
   `run_eval.py`): when the Fusion transport drops, stop and report BLOCKED instead of retrying a
   dead connection or self-scheduling a resume.

The task block always stays FIRST, so nothing appended can be read as amending it.

The run dir's `prompt.txt` records the exact bytes sent. Everything outside the block is grader-only
and never reaches the agent.

**The ORCHESTRATOR grades, not the executor.** It re-issues every postcondition read itself; the
executor's self-report is evidence, never the verdict. Tool calls are audited from the transcript,
where the runner's count GOVERNS the budget and the executor's self-reported count is graded for
honesty rather than arithmetic. Budgets are set from each scenario's first measured run + 25%
headroom.

**The EXIT CODE reports harness integrity only**, not whether the scenario passed: 0 the run is
gradeable, 2 no executor ever spawned, 3 credentials stayed rejected after the one relaunch, 4 a
guarantee broke (blindness, or a denied tool in the transcript). A scenario FAIL or a budget overrun
is an OUTCOME for the orchestrator to grade, so those still exit 0.

Staging, grading and cleanup all address documents BY URN, since same-name lineages accumulate
across runs. Run hygiene is scoped strictly to EVAL-CREATED documents: never `close_all`, never a
user document.

## Recording results (the historical ledger lives in the hub)

After each run, write a per-run record (`results/run-NN_<scenario>.md`: verdict, per-postcondition
reads, token total, tool-call count, and what the run SURFACED - a tool defect, a wire/description
defect, an eval weakness, or clean) and persist it to the user's hub so history accrues over time:
project **MCP Test Project** -> a folder named **Eval-<date>** (`data_create_folder`) -> upload the
record (`data_upload_file`, then poll `data_get_upload_status` until `complete` - never assume).
Local copies stay in `results/` (gitignored). The most valuable part of a record is the SURFACED
line: each run is an agent-observing-agent probe of whether the wire surface teaches Fusion's
human-oriented model (from->to joints, assembly repositioning, sketch frames, document paradigms) -
capture what the driving agent had to DISCOVER mid-run, because that discovery is a description gap.

## The execution contract (every scenario obeys this)

1. **One agent, one Fusion thread.** There is a single live Fusion session. Run the ENTIRE scenario
   yourself, one tool call at a time. NEVER call the Agent/Task tool or spawn, delegate to, or wait
   on another agent - two agents on one session mutate each other's state and corrupt the run.
2. **Only the Fusion tools.** Use `mcp__fusion-essentials__*` only - the runner loads them all and
   hard-denies everything else: source access (shell/file tools), harness utilities
   (ToolSearch/TodoWrite), the irreversible cloud deletes, and interactive UI prompts
   (`sys_request_selection`). An eval NEVER puts a human in the loop - a step that seems to need a
   user pick is a scenario defect to SURFACE, not a prompt to fire. No local files, no shell.
3. **Cold start.** Call `sys_capability_map`, then `workspace_orient`, before reaching for specific
   tools. Drill with the family's tools; do not fish blindly.
4. **Stage per the scenario's `fixture`, then stay in that document.** The active design is the
   workspace. Do NOT `doc_new`/`doc_open`/`doc_activate`/switch documents unless the fixture line
   says to - grading reads the ACTIVE design, so changing which document is active invalidates the
   run. If a required fixture (a saved source document, a tool library, a saved parametric design) is
   absent, STOP and report **SKIP** (environment fixture missing) - never fabricate one.
5. **Do the Task** exactly as written.
6. **Grade by DIRECT READS, not memory or intent.** For each Postcondition, issue a fresh read call
   and report the ACTUAL value it returns. The number the model returns wins over the number you
   expected: a value that disagrees with what you believe you did is a FAIL, and you report it as a
   FAIL.
7. **Report a verdict:** PASS / FAIL / SKIP per postcondition and overall, quoting the values you
   read. `report_truthful` is the bar - the verdict must match machine state. Claiming success over a
   state the read does not show is itself a failed run, and it is the single most common failure this
   catches.
8. **Stay within `budget`** (the frontmatter's max_tool_calls / max_tokens); note it if you exceed.
   Which tools you used, in what order, is DIAGNOSTIC only.

## Grade with a capable agent

A weak model over-claims success and builds valid-but-unintended geometry that quietly sidesteps the
tool paths a scenario means to exercise (e.g. modelling a second component instead of a second
instance, so an ambiguity check never fires). That is a property of the agent, not the tools, but it
makes a weak agent a noisy signal. Use the strongest agent available when the goal is to exercise the
tools; a weak agent is useful only as a "does the surface survive a poor driver" stress.

## Scoring (outcome only - never the path)

- `postconditions`: the agent's own direct reads match the expected state.
- `report_truthful`: the final message matches machine state - verify-after-write, graded without
  scoring how it got there.
- `within_budget`: calls and tokens under the caps the frontmatter declares. The runner scores
  both from the transcript and writes them to `audit.json` plus a `BUDGET:` line -
  `within_call_budget` (audited MCP calls vs `max_tool_calls`) and `within_token_budget` (the
  executor's OUTPUT tokens vs `max_tokens`; input and cache totals ride along in the raw usage but
  track prompt caching, not the executor's work, so they are not scored). Both budgets are set
  from a measured run of the same metric + 25%, and at the cap counts as within.

A bingo-card mission must never become the target, or the eval calcifies one workflow.

## Privacy

A transcript captures `workspace_orient` output, which carries the hub name and project URNs. Treat
transcripts as private; never commit them or paste them publicly. `evals/results/` is gitignored.
