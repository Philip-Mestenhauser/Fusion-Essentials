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
import types

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
                 "Save into Stage-v1/{{RUN_FOLDER}}\n```\n\n## Grader notes\n\nnever sent\n")

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
        assert prompt == ("Save into Stage-v1/Eval-20260201-204000-S\n\n"
                          + run_eval.CONNECTION_LOST)

    def test_a_declared_skill_is_appended_after_the_task_block(self, tmp_path, monkeypatch):
        self._skill_on_disk(tmp_path, monkeypatch)
        path = tmp_path / "S.md"
        path.write_text(self._SCENARIO.replace("id: X", "id: X\nskill: p"), encoding="utf-8")
        prompt, skill = run_eval.extract_prompt(str(path), "Eval-20260201-204000-S")
        assert skill == "p"
        # the task stays FIRST, the practice sits between it and the connection rule
        assert prompt.startswith("Save into Stage-v1/Eval-20260201-204000-S\n\n")
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
        monkeypatch.setattr(sys, "argv", ["run_eval.py", scenario, "--executor", "cli"])
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        launches = []

        def fake_launch(prompt, run_dir, model, max_turns, deny=()):
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
        monkeypatch.setattr(sys, "argv", ["run_eval.py", scenario, "--executor", "cli"])
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        launches = []

        def fake_launch(prompt, run_dir, model, max_turns, deny=()):
            launches.append(run_dir)
            transcript = os.path.join(run_dir, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": "", "usage": {}}) + "\n")
            return transcript, "Error: Not logged in. Please run /login", False, False

        monkeypatch.setattr(run_eval, "launch", fake_launch)
        code = run_eval.main()
        assert code == run_eval.EXIT_AUTH and len(launches) == 2


class TestDeniedTools:
    """The control arm of an A/B: one fusion tool denied for the run, recorded in the run dir."""

    def test_the_standing_list_is_unchanged_when_nothing_is_denied(self):
        assert run_eval.disallowed_for([]) == run_eval.DISALLOWED

    def test_a_denied_tool_is_appended_after_the_standing_list(self):
        guidance = "mcp__fusion-essentials__sys_get_guidance"
        assert run_eval.disallowed_for([guidance]) == run_eval.DISALLOWED + "," + guidance

    def test_the_denied_names_reach_the_launch_and_the_run_dir(self, tmp_path, monkeypatch):
        scenario = tmp_path / "S0_X.md"
        scenario.write_text("---\nid: X\n---\n\n## AGENT PROMPT (verbatim)\n\n```\nbuild it\n```\n",
                            encoding="utf-8")
        monkeypatch.setattr(run_eval, "_RESULTS", str(tmp_path / "results"))
        monkeypatch.setattr(run_eval, "preflight_server", lambda: None)
        guidance = "mcp__fusion-essentials__sys_get_guidance"
        monkeypatch.setattr(sys, "argv", ["run_eval.py", str(scenario), "--executor", "cli",
                                          "--deny", guidance])
        seen = {}

        def fake_launch(prompt, run_dir, model, max_turns, deny=()):
            seen.update(run_dir=run_dir, deny=list(deny))
            transcript = os.path.join(run_dir, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": "", "usage": {}}) + "\n")
            return transcript, "", False, True

        monkeypatch.setattr(run_eval, "launch", fake_launch)
        run_eval.main()
        assert seen["deny"] == [guidance]
        with open(os.path.join(seen["run_dir"], "denied.txt"), encoding="utf-8") as fh:
            assert fh.read().strip() == guidance


class TestApiToolDefinitions:
    """The tool surface an API run hands the model: the server's tools, minus the denied ones."""

    _TOOLS = [{"name": "doc_get", "description": "read the doc",
               "inputSchema": {"type": "object", "properties": {"include": {"type": "array"}}}},
              {"name": "data_delete_file", "description": "delete a cloud file",
               "inputSchema": {"type": "object"}},
              {"name": "sys_get_guidance", "description": "design practice",
               "inputSchema": {"type": "object"}}]

    def test_a_definition_carries_the_servers_schema_under_the_wire_name(self):
        defs = run_eval.tool_definitions(self._TOOLS)
        doc_get = [d for d in defs if d["name"] == _MCP][0]
        assert doc_get["input_schema"] == self._TOOLS[0]["inputSchema"]
        assert doc_get["description"] == "read the doc"
        # one cache breakpoint, on the last definition - the tool block is resent every turn
        assert [d for d in defs if "cache_control" in d] == [defs[-1]]

    def test_a_denied_tool_is_absent_from_the_definitions(self):
        # Denial by omission: a tool with no definition cannot be called at all. data_delete_file
        # is a standing denial, sys_get_guidance this run's control arm.
        denied = run_eval.api_denied(["mcp__fusion-essentials__sys_get_guidance"])
        assert [d["name"] for d in run_eval.tool_definitions(self._TOOLS, denied)] == [_MCP]


class _Clock:
    """A fake clock the fake client advances INSIDE create(), the way a slow turn does."""

    def __init__(self, per_turn=0.0):
        self.now, self.per_turn = 0.0, per_turn

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.per_turn


class _FakeApi:
    """A Messages API stand-in: each create() answers with the next scripted reply."""

    def __init__(self, replies, clock=None):
        self.replies = list(replies)
        self.sent = []
        self.clock = clock

    def create(self, messages, tools):
        self.sent.append(list(messages))
        if self.clock:
            self.clock.tick()
        return self.replies.pop(0)


def _use(tid, **inp):
    return {"type": "tool_use", "id": tid, "name": _MCP, "input": inp}


def _reply(blocks, output_tokens=0, stop_reason="end_turn"):
    return {"content": blocks, "stop_reason": stop_reason,
            "usage": {"output_tokens": output_tokens, "input_tokens": 5}}


def _ok(block):
    return {"type": "tool_result", "tool_use_id": block["id"], "content": "ok", "is_error": False}


def _loop(replies, client_clock=None, **kw):
    """(record, events) for one api_loop over a fake client and a fake server."""
    events = []
    client = _FakeApi(replies, client_clock)
    kw.setdefault("dispatch", _ok)
    record = run_eval.api_loop(client, "build it", [], events.append, **kw)
    return record, events


class TestApiLoop:
    """What stops the loop, and what it writes while it runs. The client and the server are fakes;
    a client that runs out of scripted replies is a loop that failed to stop."""

    def test_it_runs_tools_until_the_model_reports(self):
        record, events = _loop([_reply([_use("t1")]), _reply([_use("t2")]),
                                _reply([{"type": "text", "text": "FINAL: done"}], 7)])
        assert (record["calls"], record["turns"]) == (2, 3)
        assert record["final"] == "FINAL: done" and "final report" in record["stop"]
        assert [e["type"] for e in events] == ["assistant", "user", "assistant", "user",
                                               "assistant"]

    def test_the_call_over_the_budget_is_never_dispatched(self):
        record, _ = _loop([_reply([_use("t1"), _use("t2"), _use("t3")])], budget_calls=2)
        assert record["calls"] == 2 and "call budget (2 calls)" in record["stop"]

    def test_the_call_at_the_budget_still_runs(self):
        # The other side of the same boundary: at the cap the run continues to the report turn.
        record, _ = _loop([_reply([_use("t1"), _use("t2")]),
                           _reply([{"type": "text", "text": "FINAL"}])], budget_calls=2)
        assert record["calls"] == 2 and record["final"] == "FINAL"

    def test_output_tokens_exactly_at_the_budget_stop_the_run(self):
        record, _ = _loop([_reply([_use("t1")], 60), _reply([_use("t2")], 60)],
                          budget_tokens=120)
        assert record["usage"]["output_tokens"] == 120 and "token budget" in record["stop"]

    def test_one_token_under_the_budget_keeps_going(self):
        record, _ = _loop([_reply([_use("t1")], 60), _reply([_use("t2")], 60),
                           _reply([{"type": "text", "text": "FINAL"}])], budget_tokens=121)
        assert record["calls"] == 2 and record["final"] == "FINAL"

    def test_a_turn_that_takes_longer_than_the_stall_limit_ends_the_run(self):
        # The silence is INSIDE create(): the watchdog reads the idle window the moment the turn
        # lands, before anything is dispatched, so the cut tool_use never reaches the server.
        clock = _Clock(per_turn=700.0)
        events = []
        record = run_eval.api_loop(_FakeApi([_reply([_use("t1")])], clock), "build it", [],
                                   events.append, stall_s=600, dispatch=_ok, clock=clock)
        assert record["stalled"] is True and "NO PROGRESS" in record["stop"]
        assert record["calls"] == 0
        assert [e["type"] for e in events] == ["assistant"]

    def test_turns_inside_the_limit_never_trip_the_watchdog(self):
        # The counterpart: a working executor whose turns take real time is not a stall.
        clock = _Clock(per_turn=100.0)
        record, _ = _loop([_reply([_use("t1")]), _reply([_use("t2")]),
                           _reply([{"type": "text", "text": "FINAL"}])], stall_s=600, clock=clock,
                          client_clock=clock)
        assert record["stalled"] is False and record["calls"] == 2

    def test_the_max_turns_backstop_stops_the_loop(self):
        record, _ = _loop([_reply([_use("t1")])], max_turns=1)
        assert record["turns"] == 1 and "max turns (1)" in record["stop"]

    def test_a_truncated_turn_is_not_recorded_as_the_final_report(self):
        # stop_reason max_tokens means the text stops mid-thought; grading it as the report would
        # score a sentence the model never finished.
        record, _ = _loop([_reply([{"type": "text", "text": "I will now ext"}], 9,
                                  stop_reason="max_tokens")])
        assert record["final"] == "" and "max_tokens" in record["stop"]

    def test_a_refused_turn_is_not_recorded_as_the_final_report(self):
        record, _ = _loop([_reply([{"type": "text", "text": ""}], 2, stop_reason="refusal")])
        assert record["final"] == "" and "refusal" in record["stop"]

    def test_a_failing_turn_leaves_the_spend_in_the_record(self):
        # The client raises on the second turn (its scripted replies run out). The record is the
        # caller's, so the run still reports the turn and the tokens it already burned.
        record = run_eval.api_record()
        with pytest.raises(IndexError):
            run_eval.api_loop(_FakeApi([_reply([_use("t1")], 40)]), "build it", [],
                              lambda event: None, dispatch=_ok, record=record)
        assert (record["turns"], record["calls"]) == (1, 1)
        assert record["usage"]["output_tokens"] == 40

    def test_the_transcript_lines_are_the_ones_audit_reads(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        path = tmp_path / "transcript.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            def write(event):
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")

            client = _FakeApi([_reply([_use("t1")]), _reply([_use("t2")]),
                               _reply([{"type": "text", "text": "FINAL: done"}], 12)])
            record = run_eval.api_loop(client, "build it", [], write, dispatch=_ok)
            fh.write(json.dumps({"type": "result", "result": record["final"],
                                 "usage": record["usage"], "num_turns": record["turns"]}) + "\n")
        report, final = run_eval.audit(str(path), str(run_dir), 5, 1000)
        assert report["tool_calls_mcp"] == 2 and report["call_sequence"] == [_MCP, _MCP]
        assert report["output_tokens"] == 12 and report["within_token_budget"] is True
        assert report["blind"] is True and report["harness_leak"] is False
        assert final == "FINAL: done"

    def test_the_transcript_records_only_the_calls_that_ran(self, tmp_path):
        # audit counts tool_use blocks, so a block the budget cut would read as a call the server
        # never saw - and a run stopped AT its budget would read as over it.
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        path = tmp_path / "transcript.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            def write(event):
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")

            client = _FakeApi([_reply([_use("t1"), _use("t2"), _use("t3")])])
            record = run_eval.api_loop(client, "build it", [], write, budget_calls=2, dispatch=_ok)
            fh.write(json.dumps({"type": "result", "result": record["final"],
                                 "usage": record["usage"], "num_turns": record["turns"]}) + "\n")
        report, _final = run_eval.audit(str(path), str(run_dir), 2)
        assert record["calls"] == 2
        assert report["tool_calls_mcp"] == 2 and report["within_call_budget"] is True


class TestApiDispatch:
    """One tool_use block executed against the server, and the result block it becomes."""

    def test_the_server_is_called_bare_and_its_payload_comes_back_unchanged(self):
        seen = {}
        # the size a rich read answers with - a result path that truncates passes on a short one
        payload = json.dumps({"ok": True, "bodies": [{"name": f"Body{i}", "volume": i * 1.5}
                                                     for i in range(200)]})
        assert len(payload) > 4000

        def call(name, arguments):
            seen.update(name=name, arguments=arguments)
            return False, [{"type": "text", "text": payload}]

        result = run_eval.dispatch_tool_use(
            {"type": "tool_use", "id": "t1", "name": _MCP, "input": {"include": ["bodies"]}},
            call=call)
        assert seen == {"name": "doc_get", "arguments": {"include": ["bodies"]}}
        assert result == {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
                          "content": [{"type": "text", "text": payload}]}

    def test_a_mixed_text_and_image_result_keeps_every_block(self):
        blocks = [{"type": "text", "text": "front view"},
                  {"type": "image", "data": "QUJD", "mimeType": "image/png"},
                  {"type": "text", "text": "captured"}]
        result = run_eval.dispatch_tool_use({"id": "t1", "name": _MCP},
                                            call=lambda n, a: (False, blocks))
        assert result["content"] == [
            {"type": "text", "text": "front view"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "QUJD"}},
            {"type": "text", "text": "captured"}]

    def test_an_image_only_result_reaches_the_model_as_an_image(self):
        # A screenshot result often carries no text block at all; a text-only reader would hand a
        # visual-check scenario a note saying nothing came back.
        result = run_eval.dispatch_tool_use(
            {"id": "t1", "name": _MCP},
            call=lambda n, a: (False, [{"type": "image", "data": "QUJD",
                                        "mimeType": "image/jpeg"}]))
        assert [b["type"] for b in result["content"]] == ["image"]
        assert result["content"][0]["source"] == {"type": "base64", "media_type": "image/jpeg",
                                                  "data": "QUJD"}

    def test_a_refused_call_comes_back_as_an_error_result(self):
        result = run_eval.dispatch_tool_use(
            {"id": "t1", "name": _MCP},
            call=lambda n, a: (True, [{"type": "text", "text": "no body named 'Bolt'"}]))
        assert result["is_error"] is True
        assert result["content"] == [{"type": "text", "text": "no body named 'Bolt'"}]

    def test_a_result_with_no_blocks_says_that_rather_than_going_out_empty(self):
        result = run_eval.dispatch_tool_use({"id": "t1", "name": _MCP},
                                            call=lambda n, a: (False, []))
        assert result["content"] == [{"type": "text",
                                      "text": "(the result carried no content blocks)"}]


class TestDenyValidation:
    _TOOLS = [{"name": "sys_get_guidance"}, {"name": "doc_get"}]

    def test_an_unknown_deny_name_refuses_naming_it_and_the_near_miss(self):
        # A silently ignored name turns the control arm of an A/B into a treatment run, while
        # denied.txt still says the tool was withheld.
        with pytest.raises(SystemExit) as err:
            run_eval.deny_or_exit(["mcp__fusion-essentials__sys_get_guidence"], self._TOOLS)
        assert "sys_get_guidence" in str(err.value)
        assert "mcp__fusion-essentials__sys_get_guidance" in str(err.value)

    def test_a_registered_deny_name_passes(self):
        names = ["mcp__fusion-essentials__sys_get_guidance"]
        assert run_eval.deny_or_exit(names, self._TOOLS) == names


class TestApiRequestShape:
    """The request one turn sends. ApiClient.create is called against a stub, so the tests need
    neither the SDK nor a key."""

    def test_the_turn_carries_the_model_the_cap_and_the_prefix_cache_breakpoint(self):
        sent = {}

        class _Messages:
            def create(self, **kw):
                sent.update(kw)
                return types.SimpleNamespace(to_dict=lambda: {"content": []})

        client = types.SimpleNamespace(model="claude-opus-5",
                                       _client=types.SimpleNamespace(messages=_Messages()))
        reply = run_eval.ApiClient.create(client, [{"role": "user", "content": "hi"}], [])
        assert reply == {"content": []}
        assert sent["model"] == "claude-opus-5" and sent["max_tokens"] == run_eval.API_MAX_TOKENS
        # the growing message prefix caches too, beside the tool block's own breakpoint
        assert sent["cache_control"] == {"type": "ephemeral"}


class TestApiStartupRefusals:
    def test_a_missing_key_refuses_naming_the_variable(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(SystemExit) as err:
            run_eval.api_key_or_exit()
        assert "ANTHROPIC_API_KEY" in str(err.value)

    def test_a_set_key_starts_the_run(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert run_eval.api_key_or_exit() == "sk-ant-test"

    def test_a_bare_alias_is_refused_and_a_model_id_passes(self):
        # The cli executor takes aliases like 'sonnet'; the Messages API takes ids only.
        with pytest.raises(SystemExit) as err:
            run_eval.api_model_or_exit("sonnet")
        assert "sonnet" in str(err.value) and "claude-opus-5" in str(err.value)
        assert run_eval.api_model_or_exit("claude-opus-5") == "claude-opus-5"


class TestExecutorSelection:
    def test_the_run_setting_picks_the_launcher(self, monkeypatch):
        monkeypatch.setattr(run_eval, "launch_cli", lambda *a: "cli ran")
        monkeypatch.setattr(run_eval, "launch_api", lambda *a: "api ran")
        monkeypatch.setattr(run_eval, "EXECUTOR", "cli")
        assert run_eval.launch("p", "dir", "m", 1) == "cli ran"
        monkeypatch.setattr(run_eval, "EXECUTOR", "api")
        assert run_eval.launch("p", "dir", "m", 1) == "api ran"

    def test_main_hands_the_executor_and_both_budgets_to_the_loop(self, tmp_path, monkeypatch):
        # The loop enforces the budgets, so a run whose budgets never left main stops at neither.
        scenario = tmp_path / "S0_X.md"
        scenario.write_text("---\nbudget:\n  max_tool_calls: 7\n  max_tokens: 900\n---\n\n"
                            "## AGENT PROMPT (verbatim)\n\n```\nbuild it\n```\n", encoding="utf-8")
        monkeypatch.setattr(run_eval, "_RESULTS", str(tmp_path / "results"))
        monkeypatch.setattr(run_eval, "preflight_server", lambda: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(sys, "argv", ["run_eval.py", str(scenario)])

        def fake_launch(prompt, run_dir, model, max_turns, deny=()):
            transcript = os.path.join(run_dir, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": "", "usage": {}}) + "\n")
            return transcript, "", False, True

        monkeypatch.setattr(run_eval, "launch", fake_launch)
        run_eval.main()
        assert (run_eval.EXECUTOR, run_eval.BUDGET_CALLS, run_eval.BUDGET_TOKENS) == ("api", 7, 900)

    def test_a_missing_key_stops_main_before_a_run_dir_exists(self, tmp_path, monkeypatch):
        # The refusal runs on the default invocation, so it must not leave an empty run dir (and a
        # dated batch folder) behind in the results tree.
        scenario = tmp_path / "S0_X.md"
        scenario.write_text("---\nid: X\n---\n\n## AGENT PROMPT (verbatim)\n\n```\nbuild it\n```\n",
                            encoding="utf-8")
        results = tmp_path / "results"
        monkeypatch.setattr(run_eval, "_RESULTS", str(results))
        monkeypatch.setattr(run_eval, "preflight_server", lambda: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(sys, "argv", ["run_eval.py", str(scenario)])
        with pytest.raises(SystemExit) as err:
            run_eval.main()
        assert "ANTHROPIC_API_KEY" in str(err.value)
        assert not results.exists()


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
