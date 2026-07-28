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
CLI's status stream - never credential contents), and audit.json (tool-call names/counts, budget
comparison, usage, the non-MCP-call check, and the harness_leak / auth_failure auto-flags).
"""

import argparse
import json
import os
import re
import shutil
import socket
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
# a WALL. The spatial server's script hatch gets the same treatment when the spatial arm is live.
SCRIPT_HATCH = {"mcp__fusion-essentials__sys_execute_script",
                "mcp__fusion-spatial__fusion_execute_api_script"}
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

# ── optional spatial arm: armed when the fusion-spatial server is INSTALLED and
# its add-in listener answers; otherwise every prompt renders plain and nothing changes. The
# executor gets ONLY the read-only space_* metric toolset - the spatial fusion_* writes stay
# denied (the eval keeps measuring the fusion-essentials write surface; spatial adds metric
# eyes). The {{TOOLSETS}} token in a scenario picks the matching prompt line; a scenario without
# the token is unaffected. audit.json records the arm + counts spatial calls separately.
SPATIAL_ADDIN_PORT = 8767
SPATIAL_TOOLS = ("space_digest", "space_fit", "space_measure", "space_pick",
                 "space_relations", "space_section", "space_views", "space_voxels")
TOOLSETS_PLAIN = ("Use ONLY the fusion-essentials tools (they are already loaded). "
                  "No local files, no shell.")
TOOLSETS_SPATIAL = (
    "Use the fusion-essentials tools plus the fusion-spatial space_* toolset (both already "
    "loaded). For METRIC questions - size, position, clearance, containment, wall thickness - "
    "prefer the space_* exact numeric reads (space_measure, space_relations, space_digest) over "
    "screenshots; model with the fusion-essentials tools. No local files, no shell.")
# Appended to EVERY executor prompt (below), independent of the {{TOOLSETS}} token: the tools live
# behind an MCP connection that can drop mid-run. Without this the executor waits, retries the dead
# transport indefinitely, or self-schedules a resume - all of which burn budget on a lost server.
# The honest move is an immediate BLOCKED final report. ASCII only (it crosses the wire).
CONNECTION_LOST = (
    "MCP-CONNECTION-LOST RULE: if the fusion-essentials tools start failing with connection, "
    "transport, or server-unreachable errors, the MCP connection to Fusion has dropped. STOP "
    "immediately and emit your FINAL report with verdict BLOCKED, naming what was lost and the "
    "last step that succeeded. Do NOT wait, do NOT retry the dead connection indefinitely, and "
    "do NOT schedule yourself to resume later.")


def spatial_server_js():
    """Path to the fusion-spatial MCP server entry when the arm can run, else None: the built
    server must exist (env FUSION_SPATIAL_MCP overrides the sibling-checkout default) AND the
    add-in's TCP listener must answer - a server without its add-in yields an executor whose
    every spatial call errors, worse than no arm."""
    candidates = [os.environ.get("FUSION_SPATIAL_MCP") or "",
                  os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..",
                                                "fusion-spatial-mcp", "dist", "index.js"))]
    js = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not js:
        return None
    try:
        socket.create_connection(("127.0.0.1", SPATIAL_ADDIN_PORT), timeout=2).close()
    except OSError:
        return None
    return js


def extract_prompt(scenario_path, run_tag, spatial_armed=False):
    """The fenced block under '## AGENT PROMPT (verbatim)', byte-identical except the two
    sanctioned tokens: {{RUN_FOLDER}} becomes this invocation's run tag (same-name
    collisions with prior chains' artifacts are structurally impossible), and {{TOOLSETS}}
    becomes the plain fusion-essentials-only line or the spatial-armed line + metric nudge. The
    CONNECTION_LOST rule is appended to every prompt (the tools live behind a droppable MCP
    connection, so the instruction must reach an executor whether or not the scenario carries the
    {{TOOLSETS}} token). prompt.txt records the exact bytes actually sent."""
    text = open(scenario_path, encoding="utf-8").read()
    m = re.search(r"^## AGENT PROMPT \(verbatim\)\s*\n+```\n(.*?)\n```", text,
                  re.S | re.M)
    if not m:
        sys.exit(f"{scenario_path}: no '## AGENT PROMPT (verbatim)' fenced block found")
    prompt = m.group(1).replace("{{RUN_FOLDER}}", run_tag)
    prompt = prompt.replace("{{TOOLSETS}}", TOOLSETS_SPATIAL if spatial_armed else TOOLSETS_PLAIN)
    return prompt + "\n\n" + CONNECTION_LOST


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


def scenario_budget(scenario_path):
    """(max_tool_calls, max_tokens) from the frontmatter, or (None, None)."""
    text = open(scenario_path, encoding="utf-8").read()
    calls = re.search(r"^\s*max_tool_calls:\s*(\d+)", text, re.M)
    tokens = re.search(r"^\s*max_tokens:\s*(\d+)", text, re.M)
    return (int(calls.group(1)) if calls else None,
            int(tokens.group(1)) if tokens else None)


def make_scratch(run_dir, spatial_js=None):
    """An empty cwd + a sterile config dir carrying ONLY credentials + an MCP config naming
    ONLY the fusion server (+ the spatial server when that arm is live). Returns
    (cwd, config_dir, mcp_config_path)."""
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
    if spatial_js:
        servers["fusion-spatial"] = {"type": "stdio", "command": "node", "args": [spatial_js]}
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


def launch(prompt, run_dir, model, max_turns, spatial_js=None):
    """Spawn the executor and WATCH it: tail the transcript for the init event, kill the
    process within seconds if it spawned tool-less (returns dead_spawn=True), and print a
    heartbeat with the running tool-call count so a live run is visibly alive."""
    cwd, config, mcp_config = make_scratch(run_dir, spatial_js)
    exe = shutil.which("claude")
    if not exe:
        sys.exit("claude CLI not on PATH")
    allowed = ALLOWED
    if spatial_js:
        allowed = ",".join([ALLOWED] + [f"mcp__fusion-spatial__{t}" for t in SPATIAL_TOOLS])
    cmd = [exe, "-p", "--model", model, "--mcp-config", mcp_config, "--strict-mcp-config",
           "--allowedTools", allowed, "--disallowedTools", DISALLOWED,
           "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns)]
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config
    # THE DETERMINISTIC FIX for the tool-less-spawn coin flip: deferred MCP tool loading
    # races the first model call in -p mode and loses stochastically (CLI issues
    # #43298/#42148/#34131). Loading all tools UPFRONT removes the deferral and with it the
    # race: live-verified 3/3 fresh-config spawns attach every tool vs ~30% before. Costs
    # schema tokens per session - the price of a run that reliably exists.
    env["ENABLE_TOOL_SEARCH"] = "false"
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

    dead_spawn = False
    start = last_beat = time.time()
    calls_seen = 0
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
                calls_seen += line.count('"type":"tool_use"')
            now = time.time()
            if dead_spawn or (not init_checked and now - start > INIT_DEADLINE_S):
                dead_spawn = True
                proc.kill()
                print(f"  DEAD SPAWN (zero tools at init) - killed after {int(now - start)}s",
                      flush=True)
                break
            if now - last_beat >= HEARTBEAT_S:
                print(f"  [{int(now - start)}s] executor alive - ~{calls_seen} tool calls",
                      flush=True)
                last_beat = now
    proc.wait()
    out.close()
    stderr = "".join(stderr_chunks)
    # The CLI's stderr is a status stream ("Not logged in", exit diagnostics) - never credential
    # contents; recording it lets the auth-race relaunch trigger and preserves the honest record.
    with open(os.path.join(run_dir, "stderr.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stderr)
    if proc.returncode != 0 and not dead_spawn:
        print(f"executor exited {proc.returncode}; stderr tail:\n{stderr[-2000:]}", flush=True)
    return transcript, stderr, dead_spawn


# Executors get a COPY of the API credentials; the main session rotates the single-use refresh
# token, which kills an executor still holding the stale copy.
# These markers - drawn from the CLI's own not-logged-in / expired-token diagnostics -
# identify that failure so the run relaunches ONCE with a fresh copy (see main). Matched against a
# lowercased blob; contents (tokens) are never inspected or logged, only these status strings.
_AUTH_FAILURE_MARKERS = ("not logged in", "invalid api key", "oauth token has expired",
                         "oauth token expired", "authentication_error", "please run /login")


def _auth_failure(final, stderr):
    """True when the executor's output carries a credential-rejection signature - the stale-copy
    death this harness relaunches once to recover from."""
    blob = ((final or "") + "\n" + (stderr or "")).lower()
    return any(marker in blob for marker in _AUTH_FAILURE_MARKERS)


def audit(transcript_path, run_dir, budget_calls, stderr="", spatial_armed=False):
    """Parse the stream: tool calls (names, order), the final result text, usage - and the
    blindness check (every call is an allowed MCP call). Spatial calls are counted separately
    so per-run adoption stays measurable. A harness-utility leak (a denied planning/scheduling
    tool that still reached the transcript) auto-flags harness_leak; a credential-rejection
    signature in the output/stderr auto-flags auth_failure_suspected (main relaunches once)."""
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
    spatial = [c for c in calls if c.startswith("mcp__fusion-spatial__")]
    outside = [c for c in calls
               if not c.startswith("mcp__fusion-essentials__")
               and not c.startswith("mcp__fusion-spatial__")]
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
        "spatial_armed": spatial_armed,
        "spatial_calls": len(spatial),
        "spatial_call_names": sorted(set(c.replace("mcp__fusion-spatial__", "") for c in spatial)),
        "harness_utility_calls": harness_utility,
        # Auto-flag: a denied planning/scheduling tool (Skill, Monitor, Task*, ...) still reached
        # the transcript. Kept true here so no downstream report can read clean over a leak.
        "harness_leak": bool(harness_utility),
        "source_access_calls": source_access,
        "blind": not source_access,
        # Credential-rejection signature in the executor output/stderr - the stale single-use
        # refresh-token death. main relaunches once with a fresh copy on this.
        "auth_failure_suspected": _auth_failure(final, stderr),
        "num_turns": num_turns,
        "usage": usage,
        "call_sequence": calls,
    }
    with open(os.path.join(run_dir, "audit.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    with open(os.path.join(run_dir, "report.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(final)
    return report, final


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", help="path to a scenarios/*.md file")
    ap.add_argument("--model", default="sonnet", help="executor model (default sonnet)")
    ap.add_argument("--max-turns", type=int, default=None,
                    help="hard runaway backstop (default: max(120, 2x the scenario's max_tool_calls) - "
                         "so a big-budget scenario is not severed mid-report; pass a value to override)")
    ap.add_argument("--no-spatial", action="store_true",
                    help="force the plain fusion-essentials-only arm even when the optional "
                         "fusion-spatial server is installed and its add-in answers")
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
    run_tag = time.strftime("Eval-%Y%m%d-%H%M")

    budget_calls, _ = scenario_budget(scenario)
    # --max-turns default is DERIVED from the scenario's own call budget when not passed explicitly:
    # 120 sat below several scenario budgets (S1 154, S2a 130, S7 138) and severed a run at turn 121
    # pre-report. 2x the budget leaves headroom for the report turns; the explicit flag still wins.
    max_turns = args.max_turns
    if max_turns is None:
        max_turns = max(120, 2 * budget_calls) if budget_calls else 120
    spatial_js = None if args.no_spatial else spatial_server_js()
    prompt = extract_prompt(scenario, run_tag, spatial_armed=bool(spatial_js))
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
    while True:
        n = 1
        while os.path.exists(os.path.join(batch_dir, f"run_{stem}_{n:02d}")):
            n += 1
        run_dir = os.path.join(batch_dir, f"run_{stem}_{n:02d}")
        os.makedirs(run_dir)
        print(f"run dir: {run_dir}\nmodel: {args.model}  budget: {budget_calls} calls"
              f"  max_turns: {max_turns}  cloud folder tag: {run_tag}"
              f"  spatial: {'ARMED' if spatial_js else 'absent'}", flush=True)
        transcript, stderr, dead_spawn = launch(prompt, run_dir, args.model, max_turns,
                                                spatial_js)
        report, final = audit(transcript, run_dir, budget_calls, stderr,
                              spatial_armed=bool(spatial_js))
        if dead_spawn or report["spawn_flake_suspected"]:
            if not spawn_backoffs:
                print("DEAD SPAWN persisted through all retries - giving up; the API/CLI side "
                      "is refusing tool use right now, try again later.", flush=True)
                break
            wait = spawn_backoffs.pop(0)
            print(f"DEAD SPAWN - retrying in {wait}s "
                  f"({len(spawn_backoffs)} retries left after this)...", flush=True)
            time.sleep(wait)
            preflight_server()
            continue
        if report["auth_failure_suspected"] and not auth_retry_used:
            auth_retry_used = True
            print("AUTH FAILURE (executor credentials rejected - stale single-use token) - "
                  "relaunching once with freshly copied credentials...", flush=True)
            time.sleep(5)
            preflight_server()
            continue
        break
    print(json.dumps({k: report[k] for k in
                      ("tool_calls_mcp", "spawn_flake_suspected", "within_call_budget", "blind",
                       "spatial_armed", "spatial_calls", "source_access_calls",
                       "harness_utility_calls", "harness_leak", "auth_failure_suspected",
                       "num_turns")}, indent=1))
    print("\n== executor's final report ==\n" + final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
