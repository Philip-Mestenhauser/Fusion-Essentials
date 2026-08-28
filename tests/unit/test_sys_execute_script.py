"""Unit tests for ``sys_execute_script``'s pure error-shaping helper.

The tool itself is a Fusion pass-through (deliberately not unit-tested; see tests/README.md), but
``_extract_script_error`` is pure logic worth pinning: a failing script surfaces as an OUTER
executeTextCommand traceback whose RuntimeError message embeds console noise ('MCP calling tool:
...' lines) plus the script's INNER traceback. The agent must receive the inner traceback - the
part that names the script bug - not the wrapper and the noise (live run: the signal was the last
3 lines of a 15-line blob).
"""

import json
import re
import types

import pytest

from conftest import load_tool

ses = load_tool("sys_execute_script")

_OUTER = '''Traceback (most recent call last):
  File "C:/source/tools/sys_execute_script.py", line 79, in handler
    res = app.executeTextCommand(f'Python.Run "{run_path}"')
  File "C:/adsk/core.py", line 5287, in executeTextCommand
    return _core.Application_executeTextCommand(self, command)
RuntimeError: 3 : MCP calling tool: doc_insert_occurrence
MCP calling tool: assembly_ground
MCP calling tool: joint_create
Traceback (most recent call last):
  File "<string>", line 29, in <module>
  File "<string>", line 17, in run
TypeError: in method 'Joints_createInput', argument 3 of type 'adsk::core::Ptr'
'''


class TestExtractScriptError:
    def test_returns_only_the_inner_traceback(self):
        out = ses._extract_script_error(_OUTER)
        assert out.startswith("Traceback (most recent call last):")
        assert "line 17, in run" in out
        assert "executeTextCommand" not in out  # outer wrapper frames dropped

    def test_console_noise_lines_stripped(self):
        out = ses._extract_script_error(_OUTER)
        assert "MCP calling tool" not in out

    def test_single_traceback_returned_whole(self):
        tb = 'Traceback (most recent call last):\n  File "x.py", line 1, in run\nValueError: boom'
        assert ses._extract_script_error(tb) == tb

    def test_non_traceback_text_passes_through(self):
        assert ses._extract_script_error("plain error") == "plain error"


class TestHandlerGuard:
    def test_script_without_run_function_is_rejected(self):
        res = ses.handler("print('hi')")
        assert res["isError"] is True
        assert "run" in res["content"][0]["text"]


class TestBulkMutationConstraint:
    """The description is the ONLY place a caller learns that a script has no per-item failure
    isolation - the tool cannot detect a bulk mutation, so the constraint has to be stated with the
    reason a half-failed batch reports nothing about WHICH item failed."""

    def test_description_states_one_mutation_per_call_with_its_reason(self):
        desc = ses.TOOL_DESCRIPTION
        assert "ONE mutation per call" in desc
        assert "isolation" in desc                       # the reason, not a bare rule
        assert "WHICH item failed" in desc

    def test_description_states_the_uncaught_rule_and_refuses_to_promise_more(self):
        # Two measured facts, and the reason one-mutation-per-call is safe: on a DESIGN document an
        # UNCAUGHT raise of either kind always takes the whole script (and its printed output) down,
        # and catching an error guarantees NOTHING - one caught API error left its mutation standing
        # while another rolled three back. Both "a plain Python error can leave earlier mutations
        # committed" and "a caught exception lets the earlier mutations commit" are wrong; neither
        # may reappear.
        desc = ses.TOOL_DESCRIPTION
        assert "UNCAUGHT" in desc and "always rolls the WHOLE script back" in desc
        assert "DESIGN document" in desc                  # the rollback promise is SCOPED, not global
        assert "NO GUARANTEE" in desc and "even when caught" in desc
        assert "plain Python error can leave earlier mutations committed" not in desc
        assert "lets execution continue" not in desc

    def test_description_scopes_the_rollback_promise_off_drawing_documents(self):
        # The rollback claim is measured BOTH ways on drawing documents - one rig left the ghost
        # sheet an aborted script added, another cleaned it up. So the wire may promise neither
        # outcome there. One sentence carries the rule; the description is a scarce surface and the
        # evidence + the re-read advice live on the failure result instead.
        desc = ses.TOOL_DESCRIPTION
        assert "DRAWING document" in desc
        assert "NOT GUARANTEED" in desc
        assert "always rolls the WHOLE script back on a DRAWING" not in desc
        assert "leaves the sheet" not in desc             # never the opposite always-claim either
        assert "Re-read the sheets" not in desc           # the advice belongs on the result

    def test_description_carries_the_measured_products_trap(self):
        # Measured on a DRAWING document: a script that reads a document's .products dies at the
        # executeTextCommand level, and app.activeProduct is the route in. The caller cannot recover
        # this after the fact - the call it kills is the one that would have taught it.
        desc = ses.TOOL_DESCRIPTION
        assert ".products" in desc and "app.activeProduct" in desc
        assert "measured killing the call" in desc

    def test_description_says_the_channel_can_die_mid_session(self):
        # Measured: this channel stopped executing mid-session while the typed tools kept working.
        # It sits on the prefer-a-typed-tool sentence, which is the action it implies.
        desc = ses.TOOL_DESCRIPTION
        assert "dying mid-session" in desc and "typed tools kept working" in desc
        assert "prefer a typed tool" in desc

    def test_description_stays_under_the_wire_ceiling(self):
        # The description is the surface every connected agent pays for on every turn; the ceiling
        # is a hard lint, so a scoping fix that breaches it is not a fix.
        assert len(ses.TOOL_DESCRIPTION) <= 1300


class TestDrawingFailureAdvice:
    """The re-read advice fires on a FAILURE, and only on a drawing document - that is exactly when
    the caller cannot tell whether the script's earlier work survived."""

    def test_a_failure_on_a_drawing_carries_the_re_read_advice(self, monkeypatch):
        monkeypatch.setattr(ses._drawing_common, "active_drawing", lambda: object())
        res = ses._error_result("Traceback ... boom")
        text = res["content"][0]["text"]
        assert "rollback is not guaranteed either way here" in text
        assert "Re-read the sheets" in text
        assert res["message"] == "Script execution failed"   # the terse contract is unchanged

    def test_a_failure_on_a_design_document_stays_clean(self, monkeypatch):
        monkeypatch.setattr(ses._drawing_common, "active_drawing", lambda: None)
        text = ses._error_result("Traceback ... boom")["content"][0]["text"]
        assert text == "Traceback ... boom"
        assert "sheets" not in text

    def test_module_docstring_carries_the_same_non_guarantee_and_drawing_scope(self):
        # The docstring is where the next reader of this file forms their model, so the caught-error
        # non-guarantee has to hold THERE too - a correct description over a docstring still
        # promising rollback (or survival) teaches the wrong thing at the point of use. The drawing
        # scope rides with it for the same reason.
        doc = ses.__doc__
        assert "no guarantee" in doc.lower()
        assert "even when caught" in doc
        assert "DESIGN documents" in doc
        assert "DRAWING document" in doc
        assert "not guaranteed" in doc.lower()


_SCRIPT = "def run(context):\n    print('hello')\n"


class _FakeApp:
    """Records every text command. Answers MCP.Execute from a canned reply (or raises), and reads
    the temp file the read-only loader points at while it still exists - that file's contents are
    what the real channel would exec."""

    def __init__(self, reply=None, raises=None):
        self.commands = []
        self.logged = []
        self.script_file_text = None
        self.activeDocument = types.SimpleNamespace(isValid=True)
        self._reply = reply
        self._raises = raises

    def executeTextCommand(self, command):
        self.commands.append(command)
        if command.startswith("MCP.Execute"):
            m = re.search(r"path = '([^']+)'", command)
            if m:
                with open(m.group(1), encoding="utf-8") as fh:
                    self.script_file_text = fh.read()
            if self._raises is not None:
                raise RuntimeError(self._raises)
            return self._reply
        return ""

    def log(self, text):
        self.logged.append(text)


@pytest.fixture
def run_script(monkeypatch):
    def _run(reply=None, raises=None, script=_SCRIPT, read_only=True):
        app = _FakeApp(reply=reply, raises=raises)
        monkeypatch.setattr(ses, "app", app)
        monkeypatch.setattr(ses._drawing_common, "active_drawing", lambda: None)
        return app, ses.handler(script, read_only=read_only)
    return _run


def _payload_of(command):
    """The JSON object MCP.Execute was handed, unescaped back out of the quoted parameter."""
    assert command.startswith('MCP.Execute "') and command.endswith('"')
    return json.loads(command[len('MCP.Execute "'):-1].replace('\\"', '"'))


class TestReadOnlyRouting:
    def test_read_only_goes_through_mcp_execute_with_the_flag_set(self, run_script):
        app, _ = run_script(reply=json.dumps({"message": "", "success": True}))
        assert len(app.commands) == 1, app.commands
        payload = _payload_of(app.commands[0])
        assert payload["featureType"] == "script"
        assert payload["object"]["readOnly"] is True

    def test_read_only_opens_no_transaction(self, run_script):
        # There is no design change to group into one undo step, and PTransaction.Start on a
        # read-only run would be an undo step that can never contain anything.
        app, _ = run_script(reply=json.dumps({"message": "", "success": True}))
        assert not [c for c in app.commands if c.startswith("PTransaction")]

    def test_the_callers_script_is_never_inlined_into_the_text_command(self, run_script):
        # The parameter is a quoted JSON string whose inner quotes are backslash-escaped; a script
        # carrying its own quotes/backslashes would be at the mercy of that escaping. It stays in the
        # temp file and a fixed loader execs it, so only the loader crosses the parser.
        script = 'def run(context):\n    print("a \\" b \\\\ c")\n'
        app, _ = run_script(reply=json.dumps({"message": "", "success": True}), script=script)
        loader = _payload_of(app.commands[0])["object"]["script"]
        assert 'a \\" b' not in loader
        assert "exec(compile(" in loader

    def test_the_file_the_loader_execs_is_the_sentinel_wrapped_script(self, run_script):
        app, _ = run_script(reply=json.dumps({"message": "", "success": True}))
        text = app.script_file_text
        assert text.startswith('print("%s")' % ses._RUN_SENTINEL)
        assert _SCRIPT in text
        assert text.endswith("run(None)")

    def test_write_mode_still_uses_python_run_inside_a_transaction(self, run_script):
        app, res = run_script(reply=None, read_only=False)
        assert res["isError"] is False
        assert any(c.startswith('Python.Run "') for c in app.commands)
        assert "PTransaction.Start \"Fusion-Essentials MCP Script\"" in app.commands
        assert "PTransaction.Commit" in app.commands
        assert not any(c.startswith("MCP.Execute") for c in app.commands)


class TestReadOnlyResultHonesty:
    def test_success_returns_the_output_after_this_runs_sentinel(self, run_script):
        reply = json.dumps({"message": "MCP calling tool: sys_execute_script\nstale banner\n"
                                       + ses._RUN_SENTINEL + "\nhello\n", "success": True})
        _, res = run_script(reply=reply)
        assert res["isError"] is False
        assert res["content"][0]["text"] == "hello"

    def test_only_the_output_after_the_LAST_sentinel_is_returned(self, run_script):
        # The cut is rfind, not find. The console text a channel hands back ACCUMULATES across
        # calls, so one reply can carry a preceding call's sentinel AND this call's. Cutting at the
        # FIRST ships the preceding call's output - stale results presented as this script's, which
        # is worse than no output at all because it looks like an answer.
        reply = json.dumps({"message": ses._RUN_SENTINEL + "\nSTALE from an earlier call\n"
                                       + ses._RUN_SENTINEL + "\nfresh\n", "success": True})
        _, res = run_script(reply=reply)
        assert res["content"][0]["text"] == "fresh"
        assert "STALE" not in res["content"][0]["text"]

    def test_a_failed_run_is_an_error_not_a_silent_ok(self, run_script):
        # The channel reports a script failure as success=false WITH a payload, not as a raise
        # (measured: a NameError came back that way). Reading only the text would report ok for a
        # script that never ran.
        reply = json.dumps({"error": ses._RUN_SENTINEL + "\nTraceback...\nNameError: nope",
                            "success": False})
        _, res = run_script(reply=reply)
        assert res["isError"] is True
        assert "NameError: nope" in res["content"][0]["text"]

    def test_an_unreadable_result_shape_is_an_error_not_an_ok(self, run_script):
        _, res = run_script(reply="not json at all")
        assert res["isError"] is True
        assert "could not read" in res["content"][0]["text"]
        assert "not json at all" in res["content"][0]["text"]

    def test_a_json_result_without_success_is_an_error(self, run_script):
        _, res = run_script(reply=json.dumps({"message": "hello"}))
        assert res["isError"] is True
        assert "could not read" in res["content"][0]["text"]

    def test_a_build_without_the_channel_says_so_instead_of_leaking_a_traceback(self, run_script):
        _, res = run_script(raises="3 : There is no command MCP.Execute. Use ? to get help")
        assert res["isError"] is True
        text = res["content"][0]["text"]
        assert "read_only is unavailable in this Fusion build" in text
        assert "Traceback" not in text

    def test_the_enforcement_refusal_reaches_the_caller_as_the_script_error(self, run_script):
        # The measured shape of a blocked mutation: executeTextCommand raises. That is the script's
        # own error and must not be swallowed by the missing-command branch.
        _, res = run_script(raises="3 : Cannot modify the design from a read-only context")
        assert res["isError"] is True
        assert "Cannot modify the design from a read-only context" in res["content"][0]["text"]
        assert "read_only is unavailable" not in res["content"][0]["text"]


class TestRunSentinelCut:
    def test_console_text_before_the_sentinel_is_cut_from_a_failure(self):
        # Python.Run's message embeds console text ACCUMULATED since the last run - an earlier
        # call's error banner included (measured: a timed-out cam_create_machine traceback arrived
        # inside a later script's result). Text before THIS run's sentinel never ships.
        stale = ("===== Error =====\nMCP tool 'cam_create_machine'\n"
                 "Traceback (most recent call last):\n  File \"old\", line 1\nException: timeout\n")
        fresh = ("Traceback (most recent call last):\n  File \"script\", line 3, in run\n"
                 "NameError: name 'x' is not defined")
        tb = stale + ses._RUN_SENTINEL + "\n" + fresh
        out = ses._extract_script_error(tb)
        assert "cam_create_machine" not in out
        assert "NameError" in out

    def test_a_traceback_without_a_sentinel_is_handled_whole(self):
        tb = "Traceback (most recent call last):\n  File \"s\", line 1\nValueError: boom"
        assert "ValueError: boom" in ses._extract_script_error(tb)
