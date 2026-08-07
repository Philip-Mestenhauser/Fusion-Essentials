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
        # Two measured facts, and the reason one-mutation-per-call is safe: an UNCAUGHT raise of
        # either kind always takes the whole script (and its printed output) down, and catching an
        # error guarantees NOTHING - one caught API error left its mutation standing while another
        # rolled three back. Both "a plain Python error can leave earlier mutations committed" and
        # "a caught exception lets the earlier mutations commit" are wrong; neither may reappear.
        desc = ses.TOOL_DESCRIPTION
        assert "UNCAUGHT" in desc and "always rolls the WHOLE script back" in desc
        assert "NO GUARANTEE" in desc and "even when caught" in desc
        assert "plain Python error can leave earlier mutations committed" not in desc
        assert "lets execution continue" not in desc

    def test_module_docstring_carries_the_same_non_guarantee(self):
        # The docstring is where the next reader of this file forms their model, so the caught-error
        # non-guarantee has to hold THERE too - a correct description over a docstring still
        # promising rollback (or survival) teaches the wrong thing at the point of use.
        doc = ses.__doc__
        assert "no guarantee" in doc.lower()
        assert "even when caught" in doc
