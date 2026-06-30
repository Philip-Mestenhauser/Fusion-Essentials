# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""High-risk MCP tool: execute arbitrary Fusion API Python in the live session.

This is the general "go do X" escape hatch. It is NOT registered unless the user
has explicitly enabled it (mcpServer settings -> allow_execute_api_script, default
False), because it lets a connected AI run any code in the user's CAD session.

Safety mechanics:
  - The script must define `def run(context):` (mirrors a Fusion script entry).
  - Execution is wrapped in a Fusion transaction (PTransaction) so the script's changes are
    GROUPED as a single timeline/undo step, and committed on success.
  - CAVEAT (do not over-trust this): the script runs via `Python.Run`, a NESTED interpreter, and
    the appended `run(None)` executes INSIDE it. An exception raised by the script is caught within
    Python.Run and returned to us as a result STRING — it does NOT propagate as a Python exception
    here, so the `except` branch (PTransaction.Abort) usually does NOT fire on an in-script error.
    In other words: a failing script is typically NOT auto-rolled-back; its partial changes are
    committed as one undo step. Treat this as "grouped, undoable" — NOT "atomic / rolls back on
    error". Abort only covers errors that escape Python.Run itself (rare).
  - The handler runs on Fusion's main thread (run_on_main_thread=True via Item). It is also exempt
    from the server's 30s task timeout (see server settings) precisely because a long script that
    timed out could not be interrupted yet would still commit — so it is allowed to run to
    completion rather than report a false "cancelled".
"""

import os
import re
import tempfile
import traceback

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()


def handler(script: str) -> dict:
    """Execute a Fusion API Python script string. Returns an MCP tool result dict."""
    # Require a `run` function taking a single argument (the Fusion script idiom).
    if not re.search(r'def\s+run\s*\(\s*(\w+)\s*\):', script):
        return _error_result("Script must define a 'run' function taking one argument, e.g. def run(context):")

    temp_file = None
    transaction_started = False
    transacted_doc = None
    try:
        # Python.Run executes the file but does not call run(); append the call.
        script += "\nrun(None)"

        with tempfile.NamedTemporaryFile(mode='w', prefix='fe_mcp_script', suffix='.py',
                                         delete=False, encoding='utf-8') as f:
            f.write(script)
            temp_file = f.name

        # The Python.Run text command parses the path from a quoted string. Backslashes
        # (Windows paths) can be mis-handled inside that quoted string, so normalize to
        # forward slashes, which Fusion accepts on both Windows and macOS. The path is
        # passed inside double quotes, so spaces in the path are preserved.
        run_path = temp_file.replace('\\', '/')

        # Group the script's changes into ONE timeline/undo step via a transaction. NOTE: this does
        # NOT make it atomic — an in-script exception is swallowed by Python.Run and committed as one
        # undo step, it does NOT auto-roll-back (see the module docstring's CAVEAT). "Grouped+undoable",
        # not "rolls back on error".
        try:
            transacted_doc = app.activeDocument
        except Exception:
            transacted_doc = None
        if transacted_doc:
            app.executeTextCommand('PTransaction.Start "Fusion-Essentials MCP Script"')
            transaction_started = True

        res = app.executeTextCommand(f'Python.Run "{run_path}"')

        if transaction_started and transacted_doc.isValid:
            current_doc = app.activeDocument
            if current_doc is transacted_doc:
                app.executeTextCommand('PTransaction.Commit')
            else:
                # Active document changed mid-script; commit against the original.
                transacted_doc.activate()
                app.executeTextCommand('PTransaction.Commit')
                current_doc.activate()

        result = {"isError": False, "message": "Script executed successfully"}
        if res:
            result["content"] = [{"type": "text", "text": res}]
        return result

    except Exception as e:
        if transaction_started and transacted_doc and transacted_doc.isValid:
            try:
                current_doc = app.activeDocument
                if current_doc is transacted_doc:
                    app.executeTextCommand('PTransaction.Abort')
                else:
                    transacted_doc.activate()
                    app.executeTextCommand('PTransaction.Abort')
                    current_doc.activate()
            except Exception:
                pass  # if abort itself fails, nothing more we can do
        tb = traceback.format_exc()
        app.log(f"Fusion-Essentials MCP sys_execute_script error: {e}\n{tb}")
        return _error_result(tb)
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass


def _error_result(text: str) -> dict:
    # Intentionally NOT _common.error(text): that helper mirrors the same text into both `content`
    # and `message`. Here we want a TERSE fixed `message` ("Script execution failed") while `content`
    # carries the full traceback — a deliberately different contract for arbitrary-script execution.
    return {
    "content": [{"type": "text", "text": text}],
    "isError": True,
    "message": "Script execution failed",
    }


TOOL_DESCRIPTION = (
    "Execute Fusion API Python source code in the user's live Fusion session. "
    "This is the general way to perform actions in Fusion.\n\n"
    "REQUIREMENTS:\n"
    "- The script MUST define a function `def run(context):` which is the entry point.\n"
    "- DO NOT show any modal UI (no messageBox / no input dialogs) — modal windows "
    "pause script execution and the agent cannot dismiss them.\n"
    "- Let exceptions raise rather than swallowing them, so the error text is returned. "
    "NOTE: the script's changes are grouped as ONE undo step but are NOT guaranteed to "
    "auto-roll-back on error — a partial change can commit, so verify state afterward and "
    "undo manually if needed.\n"
    "- Use print() to return values/information; printed output is included in the result.\n\n"
    "Before editing a model, consider reading its state first (e.g. workspace_orient). "
    "After changes, verify the result."
)

tool = Tool.create_with_string_input(
    name="sys_execute_script",
    description=TOOL_DESCRIPTION,
    input_param_name="script",
    input_param_description="Fusion API Python source code to execute. Must define def run(context):",
)

# enforce_timeout=False: a long script cannot be interrupted mid-run and would still COMMIT, so
# the server's 30s task timeout would only report a false failure for a change that applied. Let it
# run to completion instead. (See _execute_on_main_thread.)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False)


def register_tool():
    """Register this tool. Called only when the user has enabled it (gated)."""
    register(item)
