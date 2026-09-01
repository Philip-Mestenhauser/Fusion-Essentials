# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create a CAM milling operation in a setup: pick a strategy, a tool by (library_url, index)
reference, and add it. Generating is opt-in (generate=true) - the geometry selection comes first."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import counted, ok, error, safe
from ._cam_common import get_cam, find_setup, register_future

app = adsk.core.Application.get()


def _doc_tool_at(cam, index):
    """Fetch a Tool from THIS document's tool library by index (cam.documentToolLibrary). This is the
    library cam_edit_tools writes to at scope='document' - so an agent can create an op against a
    tool it just made in the doc, with no URL plumbing. Returns (tool, None) or (None, error)."""
    dtl = safe(lambda: cam.documentToolLibrary)
    if dtl is None:
        return None, "This document has no document tool library."
    n = safe(lambda: dtl.count, 0) or 0
    if n == 0:
        return None, ("The document tool library is empty. Add a tool first "
                      "(cam_edit_tools scope='document' action='add').")
    if not (0 <= index < n):
        return None, f"tool_index {index} out of range (document library has {n} tools)."
    t = safe(lambda: dtl.item(index))
    return (t, None) if t is not None else (None, f"No tool at document index {index}.")


def _tool_at(library_url, index):
    """Fetch a Tool by (library_url, index) - the reference handle cam_edit_tools (list) returns for a
    SHARED library (local/cloud/hub/Fusion samples). Returns (tool, None) or (None, error)."""
    libs = safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)
    if not libs:
        return None, "Tool libraries unavailable."
    url = safe(lambda: adsk.core.URL.create(library_url))
    if not url:
        return None, f"Bad tool_library_url '{library_url}'."
    lib = safe(lambda: libs.toolLibraryAtURL(url))
    if not lib:
        return None, f"Could not load tool library at '{library_url}'."
    n = safe(lambda: lib.count, 0) or 0
    if not (0 <= index < n):
        return None, f"tool_index {index} out of range (library has {n} tools)."
    t = safe(lambda: lib.item(index))
    if t is None:
        return None, f"No tool at index {index}."
    return t, None


def _strategy_names(setup):
    out = []
    for s in safe(lambda: list(setup.operations.compatibleStrategies), []) or []:
        nm = safe(lambda s=s: s.name)
        if nm:
            out.append(nm)
    return out


def handler(setup: str = "", strategy: str = "", tool_library_url: str = "",
            tool_index: int = -1, tool_scope: str = "", generate: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    cam, cerr = get_cam()
    if not cam:
        return error(cerr)

    # The resolver's own refusal is returned verbatim: it is the one place that knows whether the
    # name was ABSENT or AMBIGUOUS, and only it can say which.
    target, _names, serr = find_setup(cam, setup)
    if not target:
        return error(serr)

    strategy = (strategy or "").strip()
    strategies = _strategy_names(target)
    if strategy not in strategies:
        return error(f"Strategy '{strategy}' isn't compatible with setup '{setup}'. Compatible: "
                     f"{', '.join(strategies[:40])}.")

    # tool: document library (by index) or a shared library (by url + index)
    tool_scope = (tool_scope or "").strip().lower()
    if tool_index is None or tool_index < 0:
        return error("Provide 'tool_index' (with 'tool_scope=document' for this doc's library, or "
                     "'tool_library_url' for a shared one) - both from cam_edit_tools.")
    if tool_scope == "document":
        tool, terr = _doc_tool_at(cam, tool_index)
    elif tool_library_url:
        tool, terr = _tool_at(tool_library_url, tool_index)
    else:
        return error("Provide a tool reference: 'tool_scope=document' + 'tool_index', OR "
                     "'tool_library_url' + 'tool_index' (from cam_edit_tools).")
    if terr:
        return error(terr)

    # createInput can raise on an invalid strategy despite the check (be safe), so guard the mutation.
    try:
        opin = target.operations.createInput(strategy)
    except Exception as e:
        return error(f"createInput('{strategy}') failed: {e}")
    if not opin:
        return error(f"createInput('{strategy}') returned nothing.")
    try:
        opin.tool = tool
    except Exception as e:
        return error(f"Could not assign the tool to a '{strategy}' operation: {e}")

    # counted, not safe(): the landing gate COMPARES these two, and a non-int (an unmodelled
    # property handing back a truthy object) is not a count either side of the add.
    ops_before = counted(lambda: target.operations.count)
    op = target.operations.add(opin)        # MUTATION
    if not op:
        return error("operations.add returned no operation.")
    ops_after = counted(lambda: target.operations.count)
    # A count that does not READ cannot clear the landing, so it is UNCONFIRMED rather than a pass.
    if ops_before is None or ops_after is None:
        unread = " and ".join(word for word, value in (("before", ops_before), ("after", ops_after))
                              if value is None)
        return error(f"operations.add returned '{safe(lambda: op.name)}' but the setup's operation "
                     f"count could not be read {unread} the add, so the operation's landing is "
                     "UNCONFIRMED. Re-read the setup with cam_get(include=['operations']).")
    if ops_after <= ops_before:
        return error(f"operations.add returned '{safe(lambda: op.name)}' but the setup's operation "
                     f"count did not increase ({ops_before} before, {ops_after} after) - the "
                     "operation did not land.")

    result = {
        "operation": safe(lambda: op.name),
        "setup": setup,
        "strategy": strategy,
        "generation_started": False,
        "note": "Operation created. " + ("" if generate else
                "No toolpath yet: select the geometry it cuts with cam_select_geometry, THEN compute "
                "it (cam_generate, or generate=true here). Generating a selection-driven strategy "
                "before its geometry is selected does not fail - it leaves the operation reading "
                "valid with a selection WARNING and no toolpath (measured on a 2D Contour)."),
    }

    if generate:
        # The Future MUST be registered, not discarded: if it is garbage-collected Fusion ABANDONS
        # the in-progress generation. _cam_common.register_future is the one registration path, and
        # its handle is what cam_get_status polls.
        gerr, handle = None, None
        try:
            fut = cam.generateToolpath(op)
            if not fut:
                gerr = "generateToolpath returned no future, so no generation is running."
            else:
                op_name = safe(lambda: op.name)
                handle, _total = register_future(
                    fut, f"operation '{op_name}'", "operation", False,
                    target_name=op_name or "")
        except Exception as e:
            gerr = str(e)
        result["generation_started"] = gerr is None
        if gerr:
            result["generate_error"] = gerr
            result["note"] = f"Operation created but toolpath generation errored: {gerr}"
        else:
            # hasToolpath/isToolpathValid read this early are STALE (the generation is async) -
            # reporting them here would report a false negative, so they are deliberately omitted.
            result["generation_handle"] = handle
            result["note"] = ("Operation created; toolpath generation started (async). Poll it with "
                              f"cam_get_status(handle='{handle}'), or confirm with "
                              "cam_get(include=['operations']) once generation completes.")
    return ok(result)


TOOL_DESCRIPTION = (
    "CREATE a CAM milling operation in a setup. 'setup' = the "
    "setup name; 'strategy' = face / adaptive / pocket2d / drill / bore / contour2d / ... (validated "
    "against the setup's compatible strategies). TOOL ref: 'tool_scope=document' + 'tool_index' (this "
    "doc's library - what cam_edit_tools scope='document' adds; no URL needed) OR 'tool_library_url' "
    "+ 'tool_index' (a shared library). Order: create -> cam_select_geometry -> generate=true here "
    "(or cam_generate); 'generate' defaults to FALSE because generating before the geometry is "
    "selected yields a warned op with no toolpath. cam_create_setup makes the setup first."
)

tool = (
    Tool.create_simple(name="cam_create_operation", description=TOOL_DESCRIPTION)
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get)."})
    .add_input_property("strategy", {"type": "string",
            "description": "Strategy name, e.g. face / adaptive / pocket2d / drill / bore / contour2d."})
    .add_input_property("tool_scope", {"type": "string", "enum": ["document"],
            "description": "Set 'document' to take the tool from this doc's library by tool_index (no url)."})
    .add_input_property("tool_library_url", {"type": "string",
            "description": "Shared tool library url (from cam_edit_tools) - omit if tool_scope=document."})
    .add_input_property("tool_index", {"type": "integer",
            "description": "Tool index within the chosen library (from cam_edit_tools)."})
    .add_input_property("generate", {"type": "boolean",
            "description": "Generate the toolpath after creating (default false - select geometry first)."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # The synchronous effect only: generate=true LAUNCHES a background generation this call never
    # reads back, and the payload sends the caller to cam_get_status for it. The landing gate is the
    # setup's operation count either side of the add, unreadable included. Residual: the tool is set
    # on the INPUT and never re-read off the created operation (CAM-49).
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_create_operation.py::TestOperationLanding"
                      "::test_an_operation_that_never_lands_in_the_setup_is_an_error"))


def register_tool():
    register(item)
