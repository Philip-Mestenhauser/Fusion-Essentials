# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The blind eval runner's pure decisions: what a run is tagged, what its budgets score, when a
credential rejection earns a relaunch, and what the process exit code says.

These are the parts that decide whether a run is usable BEFORE a human reads the report - a
colliding run tag merges two runs' cloud artifacts, a replayed prompt re-runs a scenario over
already-mutated live state, and a zero exit over a broken harness reads as a clean run. Nothing
here launches an executor or touches Fusion; the transcripts are built in tmp_path.
"""

import json
import os
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live", "evals"))
import run_eval  # noqa: E402

_MCP = "mcp__fusion-essentials__doc_get"


def _transcript(tmp_path, mcp_calls=0, other_calls=(), final="", usage=None, name="t.jsonl"):
    """A minimal stream in the shape audit() parses: assistant tool_use blocks then the result."""
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        for tool in [_MCP] * mcp_calls + list(other_calls):
            fh.write(json.dumps({"type": "assistant",
                                 "message": {"content": [{"type": "tool_use", "name": tool}]}})
                     + "\n")
        fh.write(json.dumps({"type": "result", "result": final,
                             "usage": usage or {}, "num_turns": 4}) + "\n")
    return str(path)


def _audit(tmp_path, budget_calls=None, budget_tokens=None, stderr="", skill=None, **kw):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    transcript = _transcript(tmp_path, **kw)
    report, _final = run_eval.audit(transcript, str(run_dir), budget_calls, budget_tokens, stderr,
                                    skill)
    return report


class TestRunTag:
    def test_tag_carries_the_scenario_stem(self):
        # gmtime, not localtime: the epoch is fixed, so the expected string is its UTC rendering
        # and cannot depend on the machine's zone.
        tag = run_eval.run_tag_for("S6_Vise", time.gmtime(1_770_000_000))
        assert tag == "Eval-20260202-024000-S6_Vise"

    def test_two_scenarios_in_one_minute_get_different_tags(self):
        when = time.gmtime(1_770_000_000)
        assert run_eval.run_tag_for("S4_Details", when) != run_eval.run_tag_for("S8_CAM", when)

    def test_the_same_scenario_seconds_apart_gets_different_tags(self):
        # The dead-spawn retry relaunches within seconds - minute resolution hands both attempts
        # the same cloud subfolder.
        first = run_eval.run_tag_for("S4_Details", time.gmtime(1_770_000_000))
        second = run_eval.run_tag_for("S4_Details", time.gmtime(1_770_000_007))
        assert first != second and first.endswith("S4_Details")


class TestPromptAssembly:
    _SCENARIO = ("---\nid: X\n---\n\n## AGENT PROMPT (verbatim)\n\n```\n"
                 "Save into Pipeline-v1/{{RUN_FOLDER}}\n```\n\n## Grader notes\n\nnever sent\n")

    _SKILL = "---\nname: p\ndescription: >-\n  when to reach for it\n---\n\n# Practice\n\nGround one part.\n"

    def _skill_on_disk(self, tmp_path, monkeypatch, name="p"):
        skills = tmp_path / "skills"
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(self._SKILL, encoding="utf-8")
        monkeypatch.setattr(run_eval, "SKILLS_DIR", str(skills))

    def test_prompt_is_the_block_plus_exactly_the_connection_lost_rule(self, tmp_path):
        path = tmp_path / "S.md"
        path.write_text(self._SCENARIO, encoding="utf-8")
        prompt, skill = run_eval.extract_prompt(str(path), "Eval-20260201-204000-S")
        assert skill is None
        assert prompt == ("Save into Pipeline-v1/Eval-20260201-204000-S\n\n"
                          + run_eval.CONNECTION_LOST)

    def test_a_declared_skill_is_appended_after_the_task_block(self, tmp_path, monkeypatch):
        self._skill_on_disk(tmp_path, monkeypatch)
        path = tmp_path / "S.md"
        path.write_text(self._SCENARIO.replace("id: X", "id: X\nskill: p"), encoding="utf-8")
        prompt, skill = run_eval.extract_prompt(str(path), "Eval-20260201-204000-S")
        assert skill == "p"
        # the task stays FIRST, the practice sits between it and the connection rule
        assert prompt.startswith("Save into Pipeline-v1/Eval-20260201-204000-S\n\n")
        assert prompt.index("# Practice") > prompt.index("Save into")
        assert prompt.index("# Practice") < prompt.index(run_eval.CONNECTION_LOST)
        assert "Ground one part." in prompt

    def test_the_skill_frontmatter_never_reaches_the_executor(self, tmp_path, monkeypatch):
        # The frontmatter tells an agent WHEN to go looking for the skill; an executor that already
        # holds the body would only be sent hunting for a Skill tool it is denied.
        self._skill_on_disk(tmp_path, monkeypatch)
        path = tmp_path / "S.md"
        path.write_text(self._SCENARIO.replace("id: X", "id: X\nskill: p"), encoding="utf-8")
        prompt, _ = run_eval.extract_prompt(str(path), "Eval-20260201-204000-S")
        assert "description:" not in prompt and "when to reach for it" not in prompt

    def test_a_missing_skill_stops_the_run_rather_than_running_without_it(self, tmp_path,
                                                                         monkeypatch):
        # Silently dropping it would produce a run whose prompt.txt disagrees with its own
        # frontmatter - the one thing a comparison across runs cannot survive.
        self._skill_on_disk(tmp_path, monkeypatch)
        path = tmp_path / "S.md"
        path.write_text(self._SCENARIO.replace("id: X", "id: X\nskill: absent"), encoding="utf-8")
        with pytest.raises(SystemExit):
            run_eval.extract_prompt(str(path), "Eval-20260201-204000-S")

    def test_the_run_records_which_skill_it_carried(self, tmp_path):
        assert _audit(tmp_path, skill="p")["skill"] == "p"
        assert _audit(tmp_path)["skill"] is None


class TestBudgetScoring:
    def test_frontmatter_declares_both_caps(self, tmp_path):
        path = tmp_path / "S.md"
        path.write_text("budget:\n  max_tool_calls: 110\n  max_tokens: 173000\n", encoding="utf-8")
        assert run_eval.scenario_budget(str(path)) == (110, 173000)

    def test_output_tokens_are_scored_against_the_declared_cap(self, tmp_path):
        report = _audit(tmp_path, budget_tokens=1000, usage={"output_tokens": 640})
        assert report["output_tokens"] == 640
        assert report["budget_max_tokens"] == 1000
        assert report["within_token_budget"] is True

    def test_output_tokens_exactly_at_the_cap_are_within(self, tmp_path):
        report = _audit(tmp_path, budget_tokens=1000, usage={"output_tokens": 1000})
        assert report["within_token_budget"] is True

    def test_one_token_over_the_cap_is_over(self, tmp_path):
        report = _audit(tmp_path, budget_tokens=1000, usage={"output_tokens": 1001})
        assert report["within_token_budget"] is False

    def test_cache_and_input_totals_do_not_count_against_the_token_cap(self, tmp_path):
        # Cache reads dwarf the executor's own work (21M on a measured run); scoring them would
        # fail every scenario.
        report = _audit(tmp_path, budget_tokens=1000,
                        usage={"output_tokens": 900, "input_tokens": 5000,
                               "cache_read_input_tokens": 21_000_000})
        assert report["output_tokens"] == 900 and report["within_token_budget"] is True

    def test_an_undeclared_token_budget_is_not_an_overrun(self, tmp_path):
        report = _audit(tmp_path, usage={"output_tokens": 999_999})
        assert report["budget_max_tokens"] is None and report["within_token_budget"] is True

    def test_calls_exactly_at_the_cap_are_within(self, tmp_path):
        report = _audit(tmp_path, budget_calls=3, mcp_calls=3)
        assert report["tool_calls_mcp"] == 3 and report["within_call_budget"] is True

    def test_one_call_over_the_cap_is_over(self, tmp_path):
        report = _audit(tmp_path, budget_calls=3, mcp_calls=4)
        assert report["within_call_budget"] is False

    def test_the_budget_line_reports_both_actuals_against_both_caps(self):
        line = run_eval.budget_line({"tool_calls_mcp": 101, "budget_max_tool_calls": 115,
                                     "within_call_budget": True, "output_tokens": 126_755,
                                     "budget_max_tokens": 285_000, "within_token_budget": True})
        assert line == "BUDGET: calls 101/115 WITHIN; output tokens 126755/285000 WITHIN"

    def test_the_budget_line_says_over_for_the_budget_that_blew(self):
        line = run_eval.budget_line({"tool_calls_mcp": 120, "budget_max_tool_calls": 115,
                                     "within_call_budget": False, "output_tokens": 10,
                                     "budget_max_tokens": 285_000, "within_token_budget": True})
        assert "calls 120/115 OVER" in line and "output tokens 10/285000 WITHIN" in line

    def test_the_budget_line_names_an_undeclared_cap(self):
        line = run_eval.budget_line({"tool_calls_mcp": 7, "budget_max_tool_calls": None,
                                     "within_call_budget": True, "output_tokens": 8,
                                     "budget_max_tokens": None, "within_token_budget": True})
        assert line == "BUDGET: calls 7 (no budget declared); output tokens 8 (no budget declared)"


class TestAuthSignature:
    def test_a_report_quoting_a_marker_does_not_flag_auth(self, tmp_path):
        # The executor's prose can quote "not logged in" off a tool result while the credentials
        # were fine; flagging it replays the whole prompt over live state the run already mutated.
        report = _audit(tmp_path, mcp_calls=12,
                        final="The hub read said not logged in, so I reported BLOCKED.",
                        stderr="")
        assert report["auth_failure_suspected"] is False

    def test_a_marker_in_the_status_stream_flags_auth(self, tmp_path):
        report = _audit(tmp_path, stderr="Error: Not logged in. Please run /login")
        assert report["auth_failure_suspected"] is True

    def test_a_clean_status_stream_does_not_flag_auth(self, tmp_path):
        report = _audit(tmp_path, mcp_calls=3, stderr="warning: MCP server took 3s to attach")
        assert report["auth_failure_suspected"] is False

    def test_the_matcher_reads_only_the_status_stream(self):
        assert run_eval._auth_failure("OAuth token has expired") is True
        assert run_eval._auth_failure("") is False


class TestStallWatch:
    """The liveness read over a transcript, and the no-progress verdict built on it. Nothing here
    spawns the CLI: progress_counts is a pure function over lines, stall_reason over two numbers."""

    # The CLI writes its stream COMPACT (no space after a colon), which is what the line reads
    # match on; a pretty-printed line here would test a shape the runner never sees.
    _CALL = json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": _MCP}]}},
                       separators=(",", ":"))

    @staticmethod
    def _think(est):
        return json.dumps({"type": "system", "subtype": "thinking_tokens",
                           "estimated_tokens": est, "estimated_tokens_delta": 50},
                          separators=(",", ":"))

    def test_calls_and_thinking_events_are_counted_separately(self):
        lines = [self._CALL, self._think(50), self._think(200), self._CALL]
        assert run_eval.progress_counts(lines) == (2, 2, 200)

    def test_the_estimated_tokens_reported_are_the_latest_events(self):
        # The figure RESTARTS at 50 on each new thinking block, so the last event's number is what
        # the heartbeat prints - it is not a running total and never claims to be.
        assert run_eval.progress_counts([self._think(1800), self._think(50)])[2] == 50

    def test_a_transcript_with_no_thinking_reports_none_rather_than_zero(self):
        # None says "no thinking event yet"; a 0 would read as a thinking event that did nothing.
        assert run_eval.progress_counts([self._CALL]) == (1, 0, None)

    def test_thinking_alone_is_progress_even_with_no_tool_call(self):
        # The stall this watch exists for: 16 calls then only thinking. Thinking IS work.
        assert run_eval.progress_counts([self._think(50)]) == (0, 1, 50)

    def test_an_executor_idle_past_the_limit_stalls(self):
        reason = run_eval.stall_reason(600, 600, 16, 39_950)
        assert "NO PROGRESS for 600s" in reason and "~16 tool calls" in reason
        assert "39950 est. tokens" in reason

    def test_one_second_under_the_limit_is_not_a_stall(self):
        assert run_eval.stall_reason(599, 600, 16, 39_950) == ""

    def test_a_zero_limit_disables_the_watchdog(self):
        assert run_eval.stall_reason(10_000, 0, 0, None) == ""

    def test_the_reason_says_so_when_no_thinking_event_ever_arrived(self):
        assert "no thinking event yet" in run_eval.stall_reason(600, 600, 0, None)

    def test_a_thinking_only_stretch_keeps_resetting_the_idle_clock(self):
        # The stall this watch exists for is SILENCE, not silence of tool calls: 16 calls and then
        # pure thinking is a working executor, and a clock only tool calls reset would kill it.
        live = run_eval.Liveness(0.0)
        live.read(self._CALL, 1.0)
        for t in range(2, 40):
            live.read(self._think(50 * (t - 1)), float(t))
        assert (live.calls, live.thinking) == (1, 38)
        assert live.idle_s(39.0) == 0.0
        assert run_eval.stall_reason(live.idle_s(39.0), 10, live.calls, live.est_tokens) == ""
        # ... and the clock runs again once the thinking stops
        assert run_eval.stall_reason(live.idle_s(49.0), 10, live.calls, live.est_tokens) != ""

    def test_a_line_carrying_neither_leaves_the_idle_clock_where_it_was(self):
        live = run_eval.Liveness(0.0)
        live.read(self._CALL, 5.0)
        live.read('{"type":"system","subtype":"init","tools":[]}', 30.0)
        assert live.idle_s(30.0) == 25.0

    def test_the_limit_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("EVAL_STALL_S", "45")
        assert run_eval.stall_limit_s() == 45
        monkeypatch.setenv("EVAL_STALL_S", "not-a-number")
        assert run_eval.stall_limit_s() == run_eval.STALL_S_DEFAULT
        monkeypatch.delenv("EVAL_STALL_S")
        assert run_eval.stall_limit_s() == 600


class TestMainStopsOnAStall:
    """A stall is the runner's OWN kill, so main must leave the retry loop on it. Nothing spawns:
    launch is replaced by a stub that writes the transcript audit reads."""

    def _scenario(self, tmp_path):
        path = tmp_path / "S.md"
        path.write_text("---\nid: X\n---\n\n## AGENT PROMPT (verbatim)\n\n```\nbuild it\n```\n",
                        encoding="utf-8")
        return str(path)

    def test_a_stalled_launch_is_never_retried_and_exits_with_the_stall_code(self, tmp_path,
                                                                             monkeypatch):
        # A stall can kill the executor before its first call, so the same audit carries BOTH retry
        # signatures - zero MCP calls (the spawn-flake read) and, with a credential marker in the
        # CLI's stderr, an auth rejection. Either retry would replay the prompt over live state the
        # run may already have mutated.
        scenario = self._scenario(tmp_path)
        monkeypatch.setattr(run_eval, "_RESULTS", str(tmp_path / "results"))
        monkeypatch.setattr(run_eval, "preflight_server", lambda: None)
        monkeypatch.setattr(sys, "argv", ["run_eval.py", scenario])
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        launches = []

        def fake_launch(prompt, run_dir, model, max_turns):
            launches.append(run_dir)
            transcript = os.path.join(run_dir, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": "", "usage": {}}) + "\n")
            return transcript, "Error: Not logged in. Please run /login", False, True

        monkeypatch.setattr(run_eval, "launch", fake_launch)
        code = run_eval.main()
        assert code == run_eval.EXIT_STALL
        assert len(launches) == 1, f"the stalled run was relaunched {len(launches)} times"

    def test_the_same_stub_without_the_stall_does_take_a_retry(self, tmp_path, monkeypatch):
        # The counterpart that proves the test above is not passing for want of a retry path: the
        # identical zero-call, auth-marked run relaunches once when it is NOT flagged stalled.
        scenario = self._scenario(tmp_path)
        monkeypatch.setattr(run_eval, "_RESULTS", str(tmp_path / "results"))
        monkeypatch.setattr(run_eval, "preflight_server", lambda: None)
        monkeypatch.setattr(sys, "argv", ["run_eval.py", scenario])
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        launches = []

        def fake_launch(prompt, run_dir, model, max_turns):
            launches.append(run_dir)
            transcript = os.path.join(run_dir, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": "", "usage": {}}) + "\n")
            return transcript, "Error: Not logged in. Please run /login", False, False

        monkeypatch.setattr(run_eval, "launch", fake_launch)
        code = run_eval.main()
        assert code == run_eval.EXIT_AUTH and len(launches) == 2


class TestExitStatus:
    _CLEAN = {"blind": True, "harness_leak": False, "within_call_budget": True,
              "source_access_calls": [], "harness_utility_calls": []}

    def test_a_clean_run_exits_zero(self):
        code, reason = run_eval.exit_status(dict(self._CLEAN))
        assert code == 0 and "clean" in reason

    def test_dead_spawn_exhaustion_exits_nonzero_naming_it(self):
        code, reason = run_eval.exit_status(dict(self._CLEAN), dead_spawn_exhausted=True)
        assert code == run_eval.EXIT_DEAD_SPAWN and code != 0 and "dead spawn" in reason

    def test_a_second_auth_failure_exits_nonzero_naming_it(self):
        code, reason = run_eval.exit_status(dict(self._CLEAN), auth_exhausted=True)
        assert code == run_eval.EXIT_AUTH and code != 0 and "credentials" in reason

    def test_broken_blindness_exits_nonzero_naming_the_tools(self):
        report = dict(self._CLEAN, blind=False, source_access_calls=["Read", "Bash"])
        code, reason = run_eval.exit_status(report)
        assert code == run_eval.EXIT_HARNESS_INTEGRITY and "Read, Bash" in reason

    def test_a_harness_utility_leak_exits_nonzero_naming_the_tools(self):
        report = dict(self._CLEAN, harness_leak=True, harness_utility_calls=["Skill"])
        code, reason = run_eval.exit_status(report)
        assert code == run_eval.EXIT_HARNESS_INTEGRITY and "Skill" in reason

    def test_a_stalled_run_exits_with_its_own_code_naming_the_stall(self):
        code, reason = run_eval.exit_status(dict(self._CLEAN), stalled=True)
        assert code == run_eval.EXIT_STALL and code != 0
        assert "stopped progressing" in reason and "EVAL_STALL_S" in reason

    def test_no_report_at_all_exits_nonzero(self):
        code, reason = run_eval.exit_status(None)
        assert code != 0 and "no run record" in reason

    def test_a_budget_overrun_alone_still_exits_zero(self):
        # The runner records; the orchestrator grades. An overrun calibrates the scenario.
        report = dict(self._CLEAN, within_call_budget=False, within_token_budget=False)
        assert run_eval.exit_status(report)[0] == 0

    def test_each_harness_failure_has_its_own_code(self):
        assert len({run_eval.EXIT_OK, run_eval.EXIT_DEAD_SPAWN, run_eval.EXIT_AUTH,
                    run_eval.EXIT_HARNESS_INTEGRITY, run_eval.EXIT_STALL}) == 5
