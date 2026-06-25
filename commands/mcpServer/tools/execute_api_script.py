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
  - Execution is wrapped in a Fusion transaction (PTransaction) so that if the
    script raises, all changes it made are rolled back (Abort) rather than left
    half-applied. On success the transaction is committed.
  - The handler runs on Fusion's main thread (run_on_main_thread=True via Item).
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

        # Wrap in a transaction so a failing script rolls back cleanly.
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
        app.log(f"Fusion-Essentials MCP execute_api_script error: {e}\n{tb}")
        return _error_result(tb)
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass


def _error_result(text: str) -> dict:
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
    "- DO NOT catch exceptions you intend to surface — let them raise so the change "
    "is rolled back (the whole script runs in a transaction) and the error is returned.\n"
    "- Use print() to return values/information; printed output is included in the result.\n\n"
    "Before editing a model, consider reading its state first (e.g. get_session_info). "
    "After changes, verify the result."
)

tool = Tool.create_with_string_input(
    name="execute_api_script",
    description=TOOL_DESCRIPTION,
    input_param_name="script",
    input_param_description="Fusion API Python source code to execute. Must define def run(context):",
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    """Register this tool. Called only when the user has enabled it (gated)."""
    register(item)
