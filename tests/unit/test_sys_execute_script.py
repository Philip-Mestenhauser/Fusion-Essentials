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
