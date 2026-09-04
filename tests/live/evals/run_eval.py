# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Blind eval executor: run a scenario's AGENT PROMPT in a context-isolated headless agent.

Blindness is a PROPERTY of the launch, not a promise in the prompt: the executor runs with
cwd = an empty scratch directory (no repo files, no project CLAUDE.md, no project memory), a
sterile CLAUDE_CONFIG_DIR holding ONLY the copied API credentials (no user skills, hooks, or
settings), --strict-mcp-config with an .mcp.json naming ONLY the fusion-essentials HTTP server,
and --allowedTools limited to mcp__fusion-essentials__* (anything else auto-denies headless).
The transcript is the proof: audit() lists every tool the executor actually called.

The runner EXECUTES and RECORDS; it never grades. Grading stays with the orchestrator, which
re-issues each postcondition read itself (evals/README.md) - the executor's self-report is
evidence, not verdict.

Run:  py -3 tests/live/evals/run_eval.py scenarios/S1_Foundation.md --model sonnet
      (requires Fusion running + the add-in's MCP server on 127.0.0.1:27182, and the scenario's
      fixture already staged by the orchestrator)

Output: tests/live/evals/results/run_<scenario>_<n>/ holding prompt.txt (the exact bytes sent),
transcript.jsonl (the full stream), report.txt (the executor's final message), stderr.txt (the
CLI's status stream - never credential contents), and audit.json (tool-call names/counts, both
budget comparisons - audited MCP calls and executor OUTPUT tokens - usage, the non-MCP-call check,
and the harness_leak / auth_failure auto-flags).

Exit code reports HARNESS INTEGRITY, never the scenario's verdict: 0 = a run happened under the
harness's guarantees; 2 = no executor ever ran (dead spawn through every retry); 3 = credentials
stayed rejected after the one relaunch; 4 = a guarantee broke (blindness, or a denied tool in the
transcript); 5 = the executor stopped progressing and the watchdog killed it (EVAL_STALL_S).
A budget overrun and the executor's own PASS/FAIL are outcomes the orchestrator grades, so they
leave the code at 0.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

# Executor reports are agent prose and legitimately non-ASCII (a report can contain a pi); the
# Windows console default (cp1252) cannot encode that, and a crashed final print looks like a
# failed RUN when the run dir is actually complete. Print lossy rather than die.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESULTS = os.path.join(_HERE, "results")
MCP_URL = "http://127.0.0.1:27182/mcp"
ALLOWED = "mcp__fusion-essentials__*"
# Hard-denied outright (belt to the empty-cwd braces). SOURCE_ACCESS breaks blindness if called;
# harness utilities (ToolSearch/TodoWrite) are planning aids that read nothing - denied too, so a
# transcript shows MCP calls only.
SOURCE_ACCESS = {"Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit",
                 "WebFetch", "WebSearch", "Task", "Agent"}
# Irreversible cloud deletes: the scratch config does not inherit the repo's local permission
# posture, and ALLOWED would otherwise auto-approve them for a blind executor. No scenario
# legitimately deletes cloud data; hygiene is the orchestrator's job, not the executor's.
CLOUD_DELETES = {"mcp__fusion-essentials__data_delete_file",
                 "mcp__fusion-essentials__data_delete_folder"}
# Interactive UI prompts: an eval executor is headless and blind, so a selection prompt can only
# time out - or hijack the user's live Fusion session. A human in the loop belongs to skills a
# human invoked, never to an eval; a step that seems to need a user pick is a scenario defect.
INTERACTIVE_PROMPTS = {"mcp__fusion-essentials__sys_request_selection"}
# The arbitrary-script hatch: when the settings checkbox has it enabled, it would let a
# blind executor bypass every denial above AND paper over typed-wire capability gaps (observed
# live: an executor nested a component with it, masking a real model_create_component gap). The
# eval exists to prove the TYPED wire suffices; a needed step with no typed path must surface as
# a WALL.
SCRIPT_HATCH = {"mcp__fusion-essentials__sys_execute_script"}
# Harness scheduling/agency utilities: planning aids and self-scheduling tools that do nothing for
# the eval but leak into transcripts and let a blind executor stall or self-continue instead of
# reporting. Every name below (Skill, ScheduleWakeup, Cron*, Monitor, Task*, and the rest) is on
# the list because it is an OBSERVED leak. Denied so a transcript shows fusion MCP calls only.
# The glob forms (Cron*, Task*) match the CLI's tool-family wildcard, same as ALLOWED; audit() then
# asserts on any that still slip through (harness_leak) so no report reads clean over a leak.
HARNESS_UTILITY = {"TodoWrite", "ToolSearch", "Skill", "ScheduleWakeup", "Cron*", "Monitor",
                   "Task*", "SendMessage", "Workflow", "Artifact"}
# DO NOT batch-add speculative names here: batch-adding harness-tool names correlates with
# executors spawning DEAD (init tools=[]) - an unknown name in --disallowedTools appears to
# nuke the CLI's whole tool registry. Add names one at a time, on an observed leak, and verify
# the next spawn is healthy.
DISALLOWED = ",".join(sorted(
    SOURCE_ACCESS | HARNESS_UTILITY | CLOUD_DELETES | INTERACTIVE_PROMPTS | SCRIPT_HATCH))

# Appended to EVERY executor prompt (below): the tools live behind an MCP connection that can
# drop mid-run. Without this the executor waits, retries the dead transport indefinitely, or
# self-schedules a resume - all of which burn budget on a lost server. The honest move is an
# immediate BLOCKED final report. ASCII only (it crosses the wire).
CONNECTION_LOST = (
    "MCP-CONNECTION-LOST RULE: if the fusion-essentials tools start failing with connection, "
    "transport, or server-unreachable errors, the MCP connection to Fusion has dropped. STOP "
    "immediately and emit your FINAL report with verdict BLOCKED, naming what was lost and the "
    "last step that succeeded. Do NOT wait, do NOT retry the dead connection indefinitely, and "
    "do NOT schedule yourself to resume later.")


# The design-practice skill a scenario can ask for by name in its frontmatter (`skill:`). The
# executor cannot invoke a skill - the Skill tool is denied and its cwd holds no repo - so the
# skill's BODY is appended to the prompt instead, the same way for every scenario that names one.
# What this changes about the experiment is worth saying plainly: a scenario with no skill measures
# what the WIRE alone teaches an agent, and a scenario with one measures the wire plus the practice.
# The two are different experiments and their runs do not compare - which skill (if any) a run
# carried is recorded in audit.json and in prompt.txt's exact bytes.
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))),
                          ".claude", "skills")
SKILL_HEADER = (
    "DESIGN PRACTICE (guidance, not the task - the task is above). These are the practices a "
    "capable Fusion designer works by. Apply what is relevant while you build; do not recite them, "
    "and do not let them displace a single instruction in the task.")


def scenario_skill(scenario_path):
    """The skill name a scenario's frontmatter declares, or None."""
    text = open(scenario_path, encoding="utf-8").read()
    m = re.search(r"^skill:\s*(\S+)\s*$", text, re.M)
    return m.group(1) if m else None


def skill_body(name):
    """A skill's instructions - the SKILL.md below its frontmatter. The frontmatter is the loader's
    (a description telling an agent WHEN to reach for it); appending it would tell an executor that
    already has the skill to go looking for one."""
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.exists(path):
        sys.exit(f"scenario declares skill '{name}' but {path} does not exist")
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n.*?\n---\n+", text, re.S)
    return (text[m.end():] if m else text).strip()


def run_tag_for(stem, when=None):
    """The per-invocation cloud subfolder tag: SECONDS resolution AND the scenario stem. Minute
    resolution alone collides in the two ways this harness actually runs - two different scenarios
    launched inside the same minute, and one scenario retried seconds after a dead spawn - and a
    collision silently merges two runs' cloud artifacts under one folder name."""
    return "Eval-" + time.strftime("%Y%m%d-%H%M%S", when or time.localtime()) + "-" + stem


def extract_prompt(scenario_path, run_tag):
    """The fenced block under '## AGENT PROMPT (verbatim)', byte-identical except the one
    sanctioned token: {{RUN_FOLDER}} becomes this invocation's run tag (same-name
    collisions with prior chains' artifacts are structurally impossible). Then the two fixed
    additions, in this order and identically on every scenario: the practice skill the frontmatter
    declares (when it declares one), and the CONNECTION_LOST rule. The task block stays FIRST so
    nothing appended can be read as amending it. prompt.txt records the exact bytes actually sent.
    Returns (prompt, skill_name)."""
    text = open(scenario_path, encoding="utf-8").read()
    m = re.search(r"^## AGENT PROMPT \(verbatim\)\s*\n+```\n(.*?)\n```", text,
                  re.S | re.M)
    if not m:
        sys.exit(f"{scenario_path}: no '## AGENT PROMPT (verbatim)' fenced block found")
    prompt = m.group(1).replace("{{RUN_FOLDER}}", run_tag)
    skill = scenario_skill(scenario_path)
    if skill:
        prompt += "\n\n" + SKILL_HEADER + "\n\n" + skill_body(skill)
    return prompt + "\n\n" + CONNECTION_LOST, skill


def preflight_server():
    """The MCP server must answer BEFORE the executor spawns: a dead or momentarily busy server
    yields an executor with an EMPTY tool set that can only report BLOCKED (observed live: a
    dead spawn - 0 MCP calls, 1 turn). Cheap gate, clear message."""
    health = MCP_URL.rsplit("/", 1)[0] + "/health"
    for _ in range(3):
        try:
            with urllib.request.urlopen(health, timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    sys.exit(f"MCP server not reachable at {health} - is Fusion running with the add-in loaded?")


def token_total(usage):
    """The executor's OUTPUT tokens - the figure a scenario's max_tokens budget is set from (a
    measured run's output total + 25%). Input and cache totals are recorded in audit.json's raw
    usage beside it; they track prompt caching, not the executor's work, so they are not scored."""
    try:
        return int((usage or {}).get("output_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def budget_line(report):
    """The pass/fail line for BOTH declared budgets: audited MCP calls and output tokens, each
    actual-vs-cap. Recorded, not graded - a budget overrun calibrates the scenario (and leaves the
    exit code at 0); the orchestrator decides what it means for the run."""
    parts = []
    for label, actual, cap, within in (
            ("calls", report["tool_calls_mcp"], report["budget_max_tool_calls"],
             report["within_call_budget"]),
            ("output tokens", report["output_tokens"], report["budget_max_tokens"],
             report["within_token_budget"])):
        if cap is None:
            parts.append(f"{label} {actual} (no budget declared)")
        else:
            parts.append(f"{label} {actual}/{cap} {'WITHIN' if within else 'OVER'}")
    return "BUDGET: " + "; ".join(parts)


def scenario_budget(scenario_path):
    """(max_tool_calls, max_tokens) from the frontmatter, or (None, None)."""
    text = open(scenario_path, encoding="utf-8").read()
    calls = re.search(r"^\s*max_tool_calls:\s*(\d+)", text, re.M)
    tokens = re.search(r"^\s*max_tokens:\s*(\d+)", text, re.M)
    return (int(calls.group(1)) if calls else None,
            int(tokens.group(1)) if tokens else None)


def make_scratch(run_dir):
    """An empty cwd + a sterile config dir carrying ONLY credentials + an MCP config naming
    ONLY the fusion server. Returns (cwd, config_dir, mcp_config_path)."""
    cwd = os.path.join(run_dir, "scratch")
    config = os.path.join(run_dir, "claude-config")
    os.makedirs(cwd, exist_ok=True)
    os.makedirs(config, exist_ok=True)
    creds = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
    if os.path.exists(creds):
        shutil.copy(creds, os.path.join(config, ".credentials.json"))
    # Pre-seed mcp-needs-auth-cache.json so the CLI's auth-necessity check has a cache to hit.
    # NOT a proven fix: dead-spawn storms are a per-spawn race (mixed pass/fail on identical
    # configs), and this file rode one lucky bisection sample.
    # Kept because it is free and can only remove one startup roundtrip. Content is an
    # unrelated connector entry - no instructions, blindness intact.
    auth_cache = os.path.join(os.path.expanduser("~"), ".claude", "mcp-needs-auth-cache.json")
    if os.path.exists(auth_cache):
        shutil.copy(auth_cache, os.path.join(config, "mcp-needs-auth-cache.json"))
    else:
        with open(os.path.join(config, "mcp-needs-auth-cache.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}")
    servers = {"fusion-essentials": {"type": "http", "url": MCP_URL}}
    mcp_config = os.path.join(run_dir, "mcp.json")
    with open(mcp_config, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    return cwd, config, mcp_config


# A dead spawn declares itself on the FIRST transcript line: the CLI's init event carries the
# tool list, and a healthy executor has harness tools there (7 observed) while a dead one has
# EXACTLY ZERO ("Function calls are temporarily unavailable" - the model then narrates tool
# calls it cannot make, burning the whole attempt). The mcp_servers status is "pending" at init
# in HEALTHY runs too, so the tool COUNT is the only reliable discriminator. When this happens,
# /health stays 200 - a CLI/API-side init failure, not the Fusion server.
INIT_DEADLINE_S = 90       # no init event at all within this = dead spawn
HEARTBEAT_S = 60           # progress line cadence while the executor runs
STALL_S_DEFAULT = 600      # no tool call AND no thinking event for this long = a stalled executor

# MEASURED in a transcript: the CLI emits {"type":"system","subtype":"thinking_tokens",
# "estimated_tokens":N,...} while a turn thinks, and N RESTARTS at 50 on each new thinking block -
# so the event COUNT is the monotonic progress signal and N is only the figure to print.
_THINKING_EVENT = '"subtype":"thinking_tokens"'
_THINKING_TOKENS = re.compile(r'"estimated_tokens":\s*(\d+)')


def progress_counts(lines):
    """(tool calls, thinking events, the last thinking event's estimated_tokens or None) over
    transcript lines - the two counters that only grow while an executor works, plus the figure the
    heartbeat prints beside them."""
    calls = thinking = 0
    latest = None
    for line in lines:
        calls += line.count('"type":"tool_use"')
        if _THINKING_EVENT in line:
            thinking += 1
            m = _THINKING_TOKENS.search(line)
            if m:
                latest = int(m.group(1))
    return calls, thinking, latest


class Liveness:
    """An executor's running progress: tool calls, thinking events, the latest estimated_tokens and
    WHEN either counter last moved - the state the heartbeat prints and the stall watch judges."""

    def __init__(self, now):
        self.calls = self.thinking = 0
        self.est_tokens = None
        self.last_progress = now

    def read(self, line, now):
        """Fold one transcript line in. A new tool call OR a new thinking event resets the idle
        clock, so a turn that only thinks still counts as progress."""
        calls, thinks, est = progress_counts([line])
        self.calls += calls
        self.thinking += thinks
        if est is not None:
            self.est_tokens = est
        if calls or thinks:
            self.last_progress = now

    def idle_s(self, now):
        """Seconds since the last tool call or thinking event."""
        return now - self.last_progress


def stall_limit_s():
    """The no-progress seconds an executor may sit at before the watchdog kills it (EVAL_STALL_S;
    0 disables the watchdog)."""
    try:
        return max(0, int(os.environ.get("EVAL_STALL_S") or STALL_S_DEFAULT))
    except ValueError:
        return STALL_S_DEFAULT


def stall_reason(idle_s, limit_s, calls, thinking_tokens):
    """The kill line for an executor that made no tool call and emitted no thinking event for
    `limit_s` seconds, or '' while it is still making progress."""
    if limit_s <= 0 or idle_s < limit_s:
        return ""
    thinking = "no thinking event yet" if thinking_tokens is None \
        else f"last thinking event {thinking_tokens} est. tokens"
    return (f"NO PROGRESS for {int(idle_s)}s (EVAL_STALL_S={int(limit_s)}): no new tool call and no "
            f"new thinking event after ~{calls} tool calls, {thinking}")


def launch(prompt, run_dir, model, max_turns):
    """Spawn the executor and WATCH it: tail the transcript for the init event, kill the
    process within seconds if it spawned tool-less (returns dead_spawn=True), kill it when it stops
    progressing (returns stalled=True), and print a heartbeat so a live run is visibly alive."""
    cwd, config, mcp_config = make_scratch(run_dir)
    exe = shutil.which("claude")
    if not exe:
        sys.exit("claude CLI not on PATH")
    cmd = [exe, "-p", "--model", model, "--mcp-config", mcp_config, "--strict-mcp-config",
           "--allowedTools", ALLOWED, "--disallowedTools", DISALLOWED,
           "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns)]
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config
    # Deferred MCP tool loading keeps the 473 KB tools/list (135k cached tokens, measured) out
    # of every executor turn; the tool-less spawn it can race into is what the dead-spawn
    # retries below exist for. EVAL_TOOL_SEARCH=false loads every schema upfront instead.
    env["ENABLE_TOOL_SEARCH"] = os.environ.get("EVAL_TOOL_SEARCH", "true")
    # An unbounded planning turn on a ~180k context never completes (measured: 60k thinking
    # tokens and climbing with no call for 15 min); the cap keeps a turn inside the window.
    env["MAX_THINKING_TOKENS"] = os.environ.get("EVAL_MAX_THINKING", "16000")
    env.setdefault("MCP_TIMEOUT", "60000")
    transcript = os.path.join(run_dir, "transcript.jsonl")
    with open(os.path.join(run_dir, "prompt.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(prompt)

    stderr_chunks = []

    def _drain(pipe):
        for chunk in pipe:
            stderr_chunks.append(chunk)

    out = open(transcript, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", cwd=cwd, env=env)
    threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass

    dead_spawn = stalled = False
    start = last_beat = time.time()
    live = Liveness(start)
    limit = stall_limit_s()
    init_checked = False
    with open(transcript, "r", encoding="utf-8", errors="replace") as rf:
        while proc.poll() is None:
            time.sleep(2)
            while True:  # consume complete new lines only; re-seek on a partial write
                pos = rf.tell()
                line = rf.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    rf.seek(pos)
                    break
                if not init_checked and line.strip():
                    init_checked = True
                    try:
                        init = json.loads(line)
                    except ValueError:
                        init = {}
                    if init.get("type") == "system" and not init.get("tools"):
                        dead_spawn = True
                    else:
                        print(f"  init OK ({len(init.get('tools') or [])} harness tools) - "
                              "executor running", flush=True)
                live.read(line, time.time())
            now = time.time()
            if dead_spawn or (not init_checked and now - start > INIT_DEADLINE_S):
                dead_spawn = True
                proc.kill()
                print(f"  DEAD SPAWN (zero tools at init) - killed after {int(now - start)}s",
                      flush=True)
                break
            reason = stall_reason(live.idle_s(now), limit, live.calls, live.est_tokens)
            if reason:
                stalled = True
                proc.kill()
                print("  " + reason + " - killed; the transcript so far is on disk", flush=True)
                break
            if now - last_beat >= HEARTBEAT_S:
                est_text = "-" if live.est_tokens is None else str(live.est_tokens)
                print(f"  [{int(now - start)}s] executor alive - ~{live.calls} tool calls, "
                      f"{live.thinking} thinking events (latest {est_text} est. tokens), "
                      f"idle {int(live.idle_s(now))}s", flush=True)
                last_beat = now
    proc.wait()
    out.close()
    stderr = "".join(stderr_chunks)
    # The CLI's stderr is a status stream ("Not logged in", exit diagnostics) - never credential
    # contents; recording it lets the auth-race relaunch trigger and preserves the honest record.
    with open(os.path.join(run_dir, "stderr.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stderr)
    if proc.returncode != 0 and not (dead_spawn or stalled):
        print(f"executor exited {proc.returncode}; stderr tail:\n{stderr[-2000:]}", flush=True)
    return transcript, stderr, dead_spawn, stalled


# Executors get a COPY of the API credentials; the main session rotates the single-use refresh
# token, which kills an executor still holding the stale copy.
# These markers - drawn from the CLI's own not-logged-in / expired-token diagnostics -
# identify that failure so the run relaunches ONCE with a fresh copy (see main). Matched against
# the lowercased status stream; contents (tokens) are never inspected or logged, only these strings.
_AUTH_FAILURE_MARKERS = ("not logged in", "invalid api key", "oauth token has expired",
                         "oauth token expired", "authentication_error", "please run /login")


def _auth_failure(stderr):
    """True when the CLI's OWN status stream carries a credential-rejection signature - the
    stale-copy death this harness relaunches once to recover from.

    The executor's report body is deliberately NOT searched: a report is agent prose that can
    quote a marker it read off a tool result ("the hub says not logged in") while the credentials
    were fine, and a relaunch on that text replays the whole prompt against live state the run
    already mutated."""
    return any(marker in (stderr or "").lower() for marker in _AUTH_FAILURE_MARKERS)


def audit(transcript_path, run_dir, budget_calls, budget_tokens=None, stderr="", skill=None):
    """Parse the stream: tool calls (names, order), the final result text, usage - and the
    blindness check (every call is an allowed MCP call). BOTH declared budgets are scored here
    (audited MCP calls, executor output tokens). A harness-utility leak (a denied
    planning/scheduling tool that still reached the transcript) auto-flags harness_leak; a
    credential-rejection signature in the CLI's stderr auto-flags auth_failure_suspected
    (main relaunches once, and only when the executor made zero MCP calls)."""
    calls, final, usage, num_turns = [], "", {}, None
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        calls.append(block.get("name") or "?")
            elif ev.get("type") == "result":
                final = ev.get("result") or ""
                usage = ev.get("usage") or {}
                num_turns = ev.get("num_turns")
    outside = [c for c in calls if not c.startswith("mcp__fusion-essentials__")]
    source_access = [c for c in outside if c in SOURCE_ACCESS]
    harness_utility = [c for c in outside if c not in SOURCE_ACCESS]
    mcp_calls = len(calls) - len(outside)
    report = {
        "tool_calls_mcp": mcp_calls,
        # Zero MCP calls means the executor never even oriented (every scenario opens with
        # sys_capability_map) - the spawn-flake signature, not a scenario outcome.
        "spawn_flake_suspected": mcp_calls == 0,
        "budget_max_tool_calls": budget_calls,
        "within_call_budget": (budget_calls is None or mcp_calls <= budget_calls),
        # The scenario's max_tokens, scored against the executor's OUTPUT tokens - the metric every
        # budget in the scenario docs was measured in. At the cap is WITHIN, same as the calls.
        "budget_max_tokens": budget_tokens,
        "output_tokens": token_total(usage),
        "within_token_budget": (budget_tokens is None or token_total(usage) <= budget_tokens),
        "harness_utility_calls": harness_utility,
        # Auto-flag: a denied planning/scheduling tool (Skill, Monitor, Task*, ...) still reached
        # the transcript. Kept true here so no downstream report can read clean over a leak.
        "harness_leak": bool(harness_utility),
        "source_access_calls": source_access,
        "blind": not source_access,
        # Credential-rejection signature in the CLI's stderr - the stale single-use refresh-token
        # death. main relaunches once with a fresh copy on this, gated on zero MCP calls.
        "auth_failure_suspected": _auth_failure(stderr),
        "num_turns": num_turns,
        # Which practice the run carried, beside its numbers: a run under a skill and a run without
        # one are different experiments, and this file is what a later reader compares.
        "skill": skill,
        "usage": usage,
        "call_sequence": calls,
    }
    with open(os.path.join(run_dir, "audit.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    with open(os.path.join(run_dir, "report.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(final)
    return report, final


EXIT_OK = 0
EXIT_DEAD_SPAWN = 2          # no executor ever ran
EXIT_AUTH = 3                # credentials stayed rejected after the one relaunch
EXIT_HARNESS_INTEGRITY = 4   # a run happened, but a harness guarantee broke
EXIT_STALL = 5               # the executor stopped progressing and was killed


def exit_status(report, dead_spawn_exhausted=False, auth_exhausted=False, stalled=False):
    """(code, reason) - the process's verdict on THE HARNESS, never on the scenario.

    Non-zero says this run cannot be graded as an experiment: nothing ran, credentials stayed
    rejected, or the blindness/denial guarantees the launch is built on did not hold. A budget
    overrun, a FAIL verdict, and a BLOCKED report are outcomes - the orchestrator grades those, so
    they exit 0. Each path names its reason so a caller reading only the exit code and last line
    knows which failure it hit."""
    if dead_spawn_exhausted:
        return EXIT_DEAD_SPAWN, ("dead spawn persisted through every retry - no executor ever ran, "
                                 "so there is nothing to grade")
    if auth_exhausted:
        return EXIT_AUTH, ("executor credentials were rejected again after the one relaunch - the "
                           "copied token is not being accepted")
    if stalled:
        return EXIT_STALL, ("the executor stopped progressing (no tool call and no thinking event "
                            "inside EVAL_STALL_S) and was killed - the partial run is on disk, and "
                            "what it did before the stall is in its transcript")
    if not report:
        return EXIT_DEAD_SPAWN, "no run record was produced"
    if not report.get("blind", True):
        return EXIT_HARNESS_INTEGRITY, ("blindness broken - the executor reached source tools: "
                                        + ", ".join(report.get("source_access_calls") or ["?"]))
    if report.get("harness_leak"):
        return EXIT_HARNESS_INTEGRITY, ("harness-utility leak - denied tools reached the "
                                        "transcript: "
                                        + ", ".join(report.get("harness_utility_calls") or ["?"]))
    return EXIT_OK, "harness clean - the run is gradeable"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", help="path to a scenarios/*.md file")
    ap.add_argument("--model", default="sonnet", help="executor model (default sonnet)")
    ap.add_argument("--max-turns", type=int, default=None,
                    help="hard runaway backstop (default: max(120, 2x the scenario's max_tool_calls) - "
                         "so a big-budget scenario is not severed mid-report; pass a value to override)")
    args = ap.parse_args()

    scenario = os.path.abspath(args.scenario)
    stem = os.path.splitext(os.path.basename(scenario))[0]
    # Every run lands inside a DATED batch folder - one folder per eval day,
    # holding the run dirs plus the orchestrator's grades/INDEX for that batch.
    batch_dir = os.path.join(_RESULTS, "Eval-" + time.strftime("%Y-%m-%d"))
    os.makedirs(batch_dir, exist_ok=True)
    # Per-RUN cloud subfolder tag: each invocation's saves land under
    # Pipeline-v1/<run_tag> via the {{RUN_FOLDER}} token, so a run's artifacts can never
    # name-collide with a prior chain's. Chains still hand artifacts forward BY URN.
    run_tag = run_tag_for(stem)

    budget_calls, budget_tokens = scenario_budget(scenario)
    # --max-turns default is DERIVED from the scenario's own call budget when not passed explicitly:
    # 120 sat below several scenario budgets (S1 154, S2a 130, S7 138) and severed a run at turn 121
    # pre-report. 2x the budget leaves headroom for the report turns; the explicit flag still wins.
    max_turns = args.max_turns
    if max_turns is None:
        max_turns = max(120, 2 * budget_calls) if budget_calls else 120
    prompt, skill = extract_prompt(scenario, run_tag)
    preflight_server()

    # Failure signatures earn RETRIES in fresh run dirs (every dir stays on disk as the honest
    # record). A DEAD SPAWN (zero tools at init - killed by launch()'s watchdog within seconds,
    # so a retry costs seconds, not a burned attempt) retries up to 3 more times with growing
    # backoff: dead spawns arrive in consecutive streaks while /health stays 200, so
    # short-then-long waits are the honest response to an API-side wobble. A
    # credential-rejection death (the main session rotates the single-use refresh token out from
    # under the executor's COPY) still retries ONCE - the relaunch
    # re-copies the now-rotated-in token; a copy-per-relaunch never lets an executor's token
    # refresh clobber the live/main session (a shared junction would).
    report = final = run_dir = None
    # A dead spawn is a PER-SPAWN COIN FLIP (observed live: the CLI's async MCP attach races
    # the first model call and loses ~50-70% of spawns; bisected across flags, models, and
    # config contents - all exonerated by mixed results on identical configs). Retries are
    # cheap (a lost flip self-terminates in under a minute), so grind a fair number of them.
    spawn_backoffs = [5, 5, 5, 10, 10, 20, 30]
    auth_retry_used = False
    dead_spawn_exhausted = auth_exhausted = stalled = False
    while True:
        n = 1
        while os.path.exists(os.path.join(batch_dir, f"run_{stem}_{n:02d}")):
            n += 1
        run_dir = os.path.join(batch_dir, f"run_{stem}_{n:02d}")
        os.makedirs(run_dir)
        print(f"run dir: {run_dir}\nmodel: {args.model}  budget: {budget_calls} calls / "
              f"{budget_tokens} output tokens  max_turns: {max_turns}  "
              f"cloud folder tag: {run_tag}  skill: {skill or 'none'}", flush=True)
        transcript, stderr, dead_spawn, stalled = launch(prompt, run_dir, args.model, max_turns)
        report, final = audit(transcript, run_dir, budget_calls, budget_tokens, stderr, skill)
        # Before the retry branches: a stall is OUR kill, and its zero-or-few MCP calls would
        # otherwise read as a spawn flake and replay the prompt over already-mutated live state.
        if stalled:
            break
        # AUTH is diagnosed BEFORE the dead-spawn branch, because a credential rejection kills the
        # executor before its first tool call and so also reads as zero MCP calls. The relaunch is
        # gated on that zero: an executor that already called tools has MUTATED the live document,
        # and replaying the whole prompt over that state - the runner stages nothing - compounds
        # the damage rather than recovering the run.
        if report["auth_failure_suspected"] and report["tool_calls_mcp"] == 0:
            if auth_retry_used:
                auth_exhausted = True
                print("AUTH FAILURE again after the relaunch - giving up; the copied credentials "
                      "are being rejected, re-authenticate the main session.", flush=True)
                break
            auth_retry_used = True
            print("AUTH FAILURE (executor credentials rejected - stale single-use token) - "
                  "relaunching once with freshly copied credentials...", flush=True)
            time.sleep(5)
            preflight_server()
            continue
        if report["auth_failure_suspected"]:
            print(f"  auth marker in the CLI's status stream, but the executor made "
                  f"{report['tool_calls_mcp']} MCP calls - NOT relaunching (a replay with no "
                  f"restage would re-run the prompt against already-mutated live state).",
                  flush=True)
        if dead_spawn or report["spawn_flake_suspected"]:
            if not spawn_backoffs:
                dead_spawn_exhausted = True
                print("DEAD SPAWN persisted through all retries - giving up; the API/CLI side "
                      "is refusing tool use right now, try again later.", flush=True)
                break
            wait = spawn_backoffs.pop(0)
            print(f"DEAD SPAWN - retrying in {wait}s "
                  f"({len(spawn_backoffs)} retries left after this)...", flush=True)
            time.sleep(wait)
            preflight_server()
            continue
        break
    print(json.dumps({k: report[k] for k in
                      ("tool_calls_mcp", "spawn_flake_suspected", "within_call_budget",
                       "output_tokens", "within_token_budget", "blind",
                       "source_access_calls", "harness_utility_calls", "harness_leak",
                       "auth_failure_suspected", "num_turns")}, indent=1))
    print(budget_line(report))
    print("\n== executor's final report ==\n" + (final or "(no final report)"))
    code, reason = exit_status(report, dead_spawn_exhausted, auth_exhausted, stalled)
    print(f"\nHARNESS: exit {code} - {reason}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
