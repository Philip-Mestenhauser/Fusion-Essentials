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

Run:  py -3 tests/live/evals/run_eval.py scenarios/T1_Sketch-Eval.md --model sonnet
      (requires Fusion running + the add-in's MCP server on 127.0.0.1:27182, and the scenario's
      fixture already staged by the orchestrator)

Output: tests/live/evals/results/run_<scenario>_<n>/ holding prompt.txt (the exact bytes sent),
transcript.jsonl (the full stream), report.txt (the executor's final message), and audit.json
(tool-call names/counts, budget comparison, usage, and the non-MCP-call check).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESULTS = os.path.join(_HERE, "results")
MCP_URL = "http://127.0.0.1:27182/mcp"
ALLOWED = "mcp__fusion-essentials__*"
# Hard-denied outright (belt to the empty-cwd braces). SOURCE_ACCESS breaks blindness if called;
# harness utilities (ToolSearch/TodoWrite) are planning aids that read nothing - denied too, so a
# transcript shows MCP calls only.
SOURCE_ACCESS = {"Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit",
                 "WebFetch", "WebSearch", "Task", "Agent"}
DISALLOWED = ",".join(sorted(SOURCE_ACCESS | {"TodoWrite", "ToolSearch"}))


def extract_prompt(scenario_path):
    """The fenced block under '## AGENT PROMPT (verbatim)', byte-identical."""
    text = open(scenario_path, encoding="utf-8").read()
    m = re.search(r"^## AGENT PROMPT \(verbatim\)\s*\n+```\n(.*?)\n```", text,
                  re.S | re.M)
    if not m:
        sys.exit(f"{scenario_path}: no '## AGENT PROMPT (verbatim)' fenced block found")
    return m.group(1)


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
    mcp_config = os.path.join(run_dir, "mcp.json")
    with open(mcp_config, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"fusion-essentials": {"type": "http", "url": MCP_URL}}}, fh)
    return cwd, config, mcp_config


def launch(prompt, run_dir, model, max_turns):
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
    transcript = os.path.join(run_dir, "transcript.jsonl")
    with open(os.path.join(run_dir, "prompt.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(prompt)
    with open(transcript, "w", encoding="utf-8") as out:
        proc = subprocess.run(cmd, input=prompt, stdout=out, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", cwd=cwd, env=env)
    if proc.returncode != 0:
        print(f"executor exited {proc.returncode}; stderr tail:\n{proc.stderr[-2000:]}")
    return transcript


def audit(transcript_path, run_dir, budget_calls):
    """Parse the stream: tool calls (names, order), the final result text, usage - and the
    blindness check (every call is an allowed MCP call)."""
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
    mcp_calls = len(calls) - len(outside)
    report = {
        "tool_calls_mcp": mcp_calls,
        "budget_max_tool_calls": budget_calls,
        "within_call_budget": (budget_calls is None or mcp_calls <= budget_calls),
        "harness_utility_calls": [c for c in outside if c not in SOURCE_ACCESS],
        "source_access_calls": source_access,
        "blind": not source_access,
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
    ap.add_argument("--max-turns", type=int, default=120,
                    help="hard runaway backstop, well above any scenario budget")
    args = ap.parse_args()

    scenario = os.path.abspath(args.scenario)
    stem = os.path.splitext(os.path.basename(scenario))[0]
    os.makedirs(_RESULTS, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(_RESULTS, f"run_{stem}_{n:02d}")):
        n += 1
    run_dir = os.path.join(_RESULTS, f"run_{stem}_{n:02d}")
    os.makedirs(run_dir)

    prompt = extract_prompt(scenario)
    budget_calls, _ = scenario_budget(scenario)
    print(f"run dir: {run_dir}\nmodel: {args.model}  budget: {budget_calls} calls")
    transcript = launch(prompt, run_dir, args.model, args.max_turns)
    report, final = audit(transcript, run_dir, budget_calls)
    print(json.dumps({k: report[k] for k in
                      ("tool_calls_mcp", "within_call_budget", "blind", "source_access_calls",
                       "harness_utility_calls", "num_turns")}, indent=1))
    print("\n== executor's final report ==\n" + final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
