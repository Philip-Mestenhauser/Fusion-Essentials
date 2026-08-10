"""Unit tests for ``sys_execute_script``'s pure error-shaping helper.

The tool itself is a Fusion pass-through (deliberately not unit-tested; see tests/README.md), but
``_extract_script_error`` is pure logic worth pinning: a failing script surfaces as an OUTER
executeTextCommand traceback whose RuntimeError message embeds console noise ('MCP calling tool:
...' lines) plus the script's INNER traceback. The agent must receive the inner traceback - the
part that names the script bug - not the wrapper and the noise (live run: the signal was the last
3 lines of a 15-line blob).
"""

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
