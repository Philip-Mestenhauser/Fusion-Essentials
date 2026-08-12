# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""High-risk MCP tool: execute arbitrary Fusion API Python in the live session.

The general "go do X" escape hatch. NOT registered unless the user explicitly enables it
(mcpServer settings -> allow_execute_api_script, default False). MEASURED on DESIGN documents: an
UNCAUGHT raise of either kind rolls the whole script back, printed output with it. Catching an error
is no guarantee the earlier work survived either - some Fusion API errors take the command
down even when caught. On a DRAWING document rollback is not guaranteed in either direction: the same
add-a-sheet-then-raise script has been measured leaving the sheet behind on one rig and cleaning it
up on another.
"""

import os
import re
import tempfile
import traceback

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from . import _drawing_common

app = adsk.core.Application.get()


def handler(script: str) -> dict:
    """See TOOL_DESCRIPTION."""
    # Require a `run` function taking a single argument (the Fusion script idiom).
    if not re.search(r'def\s+run\s*\(\s*(\w+)\s*\):', script):
        return _error_result("Script must define a 'run' function taking one argument, e.g. def run(context):")

    temp_file = None
    transaction_started = False
    transacted_doc = None
    try:
        # Python.Run executes the file but does not call run(); append the call. The SENTINEL print
        # is prepended as the first executed statement: Python.Run's return text embeds the
        # TextCommands console ACCUMULATED since the last run (measured: an earlier timed-out
        # handler's error banner arrived inside a later script's result), and everything before the
        # sentinel belongs to earlier calls - cut, not shipped. Costs one line of traceback offset.
        script = f'print("{_RUN_SENTINEL}")\n' + script + "\nrun(None)"

        with tempfile.NamedTemporaryFile(mode='w', prefix='fe_mcp_script', suffix='.py',
                                         delete=False, encoding='utf-8') as f:
            f.write(script)
            temp_file = f.name

        # The Python.Run text command parses the path from a quoted string. Backslashes
        # (Windows paths) can be mis-handled inside that quoted string, so normalize to
        # forward slashes, which Fusion accepts on both Windows and macOS. The path is
        # passed inside double quotes, so spaces in the path are preserved.
        run_path = temp_file.replace('\\', '/')

        # Group the script's changes into ONE timeline/undo step via a transaction. Grouping is all
        # it buys - the script cannot control what survives: measured, an UNCAUGHT raise takes the
        # whole command down and its earlier mutations with it, and CATCHING an API error is no
        # guarantee either (a caught "Bad index parameter" left its mutation standing, while a caught
        # pmiSettings raise rolled three fresh annotations back).
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
            # Python.Run's return text embeds the accumulated TextCommands console log (the same
            # noise _extract_script_error strips on the failure path). Cut at THIS run's sentinel
            # first - everything before it accumulated during earlier calls (stale error banners
            # included) - then strip the per-call log lines.
            cut = res.rfind(_RUN_SENTINEL)
            if cut != -1:
                res = res[cut + len(_RUN_SENTINEL):]
            cleaned = re.sub(r"\n{3,}", "\n\n", _CONSOLE_NOISE.sub("", res)).strip()
            if cleaned:
                result["content"] = [{"type": "text", "text": cleaned}]
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
        return _error_result(_extract_script_error(tb))
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass


# Lines the add-in logs to the TextCommands console; Python.Run's RuntimeError message embeds the
# accumulated console text, so these show up INSIDE the script-failure traceback as noise.
_CONSOLE_NOISE = re.compile(r"^MCP calling tool: .*$", re.MULTILINE)
_TB_MARKER = "Traceback (most recent call last):"
# Printed as the script's FIRST statement; console text before it accumulated during EARLIER calls.
_RUN_SENTINEL = "<<FE-SCRIPT-OUTPUT>>"


def _extract_script_error(tb: str) -> str:
    """Reduce a script-failure traceback to the SCRIPT's own error.

    A failing script surfaces as a RuntimeError from executeTextCommand whose message embeds
    (a) whatever the add-in logged to the TextCommands console since the last call and (b) the
    script's inner traceback. The outer frames (handler -> executeTextCommand) and the console
    noise say nothing about the script bug, so return just the LAST traceback block - the
    script's - with the noise lines stripped. A single-traceback text (an error outside
    Python.Run) is returned whole. The full text still goes to app.log for deep debugging."""
    cut = tb.rfind(_RUN_SENTINEL)
    if cut != -1:
        tb = tb[cut + len(_RUN_SENTINEL):]
    cleaned = _CONSOLE_NOISE.sub("", tb)
    first = cleaned.find(_TB_MARKER)
    last = cleaned.rfind(_TB_MARKER)
    if first != -1 and last > first:
        cleaned = cleaned[last:]
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# Appended to a FAILURE result on a drawing document only - the caller needs it exactly when a
# script raised there, and it would be noise on every design-document failure. The description
# carries the one-sentence rule; this carries what to DO about it.
_DRAWING_ROLLBACK_ADVICE = (
    "\n\nThe active document is a DRAWING: rollback is not guaranteed either way here - a failing "
    "script has been measured both leaving the sheet it added behind and cleaning it up. Re-read the "
    "sheets before assuming this call changed nothing."
)


def _error_result(text: str) -> dict:
    # Intentionally NOT _common.error(text): that helper mirrors the same text into both `content`
    # and `message`. Here we want a TERSE fixed `message` ("Script execution failed") while `content`
    # carries the full traceback - a deliberately different contract for arbitrary-script execution.
    if _drawing_common.active_drawing() is not None:
        text += _DRAWING_ROLLBACK_ADVICE
    return {
    "content": [{"type": "text", "text": text}],
    "isError": True,
    "message": "Script execution failed",
    }


TOOL_DESCRIPTION = (
    "Execute Fusion API Python source code in the user's live Fusion session. "
    "An escape hatch for actions the typed tools don't cover - prefer a typed tool when one "
    "exists (see sys_find_tool / sys_capability_map).\n\n"
    "REQUIREMENTS:\n"
    "- The script MUST define a function `def run(context):` which is the entry point.\n"
    "- DO NOT show any modal UI (no messageBox / no input dialogs) - modal windows "
    "pause script execution and the agent cannot dismiss them.\n"
    "- Let exceptions raise rather than swallowing them, so the error text is returned.\n"
    "- ONE mutation per call is the safe shape: a script gets no per-item failure isolation, so a "
    "bulk edit that half-fails cannot report WHICH item failed. On a DESIGN document an UNCAUGHT "
    "raise (Fusion API error or plain Python) always rolls the WHOLE script back, earlier mutations "
    "and printed output with it - and catching an error is NO GUARANTEE the earlier work survived: "
    "some Fusion API errors take the whole command down even when caught. On a DRAWING document "
    "rollback is NOT GUARANTEED either way. Batch work belongs in separate calls.\n"
    "- Use print() to return values/information; printed output is included in the result.\n\n"
    "Read the state first (e.g. workspace_orient), and verify it again after."
)

tool = Tool.create_with_string_input(
    name="sys_execute_script",
    description=TOOL_DESCRIPTION,
    input_param_name="script",
    input_param_description="Fusion API Python source code to execute. Must define def run(context):",
).strict_schema()

# enforce_timeout=False: a long script cannot be interrupted mid-run and would still COMMIT, so
# the server's 30s task timeout would only report a false failure for a change that applied. Let it
# run to completion instead. (See _execute_on_main_thread.)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False)


def register_tool():
    """Register this tool. Called only when the user has enabled it (gated)."""
    register(item)
