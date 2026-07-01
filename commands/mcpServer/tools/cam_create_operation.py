# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: CREATE (and generate) a CAM milling operation.

  cam_create_operation -> add a toolpath operation to a CAM setup: pick a STRATEGY (face / adaptive /
                          pocket2d / drill / bore / contour2d / ...), a TOOL by reference
                          (tool_library_url + tool_index - the handle cam_edit_tools / cam_get(include=['library']) hands back),
                          add it to the setup, and (by default) generate the toolpath.

The "apply an operation" half of CAM, paired with cam_edit_tools / cam_get(include=['library']) (the catalog half): read a
library to get a tool reference, then create an operation with it. cam_create_setup makes the setup;
this fills it with toolpaths.

Grounded in adsk.cam (the full path confirmed live):
  - Setup.operations.compatibleStrategies -> [OperationStrategy] (each .name is the strategy string)
  - Setup.operations.createInput(strategyName) -> OperationInput
  - OperationInput.tool = <Tool>  (a Tool from ToolLibrary.item(i) - setting it writes the tool params)
  - Setup.operations.add(input) -> Operation
  - CAM.generateToolpath(operation) -> GenerateToolpathFuture (async); operation then has
    .hasToolpath / .isToolpathValid
Handler runs on the main thread; WRITES to the document's CAM data. Be in/allow the Manufacture context.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()


# ── seams (patched in tests) ─────────────────────────────────────────────────

def _get_cam():
    """The CAM product for the active document, or (None, reason)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    for i in range(safe(lambda: doc.products.count, 0) or 0):
        p = safe(lambda i=i: doc.products.item(i))
        if p is not None and safe(lambda p=p: p.productType) == "CAMProductType":
            return adsk.cam.CAM.cast(p), None
    return None, ("No CAM data in this document. Create a setup first (cam_create_setup); switch to the "
                  "Manufacture workspace once if needed.")


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


def _find_setup(cam, name):
    name = (name or "").strip()
    for i in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda i=i: cam.setups.item(i))
        if s is not None and safe(lambda s=s: s.name) == name:
            return s
    return None


def _setup_names(cam):
    return [safe(lambda i=i: cam.setups.item(i).name)
            for i in range(safe(lambda: cam.setups.count, 0) or 0)]


def _strategy_names(setup):
    out = []
    for s in safe(lambda: list(setup.operations.compatibleStrategies), []) or []:
        nm = safe(lambda s=s: s.name)
        if nm:
            out.append(nm)
    return out


def handler(setup: str = "", strategy: str = "", tool_library_url: str = "",
            tool_index: int = -1, tool_scope: str = "", generate: bool = True) -> dict:
    """Create a CAM milling operation in a setup.

    setup: the setup name (from cam_get). strategy: e.g. face / adaptive / pocket2d / drill /
    bore / contour2d (validated against the setup's compatible strategies). The TOOL is either
    tool_scope='document' + tool_index (this doc's library - what cam_edit_tools scope='document'
    writes; no URL needed), OR tool_library_url + tool_index (a shared library). generate: generate the
    toolpath after creating (default True). WRITES.
    """
    cam, cerr = _get_cam()
    if not cam:
        return error(cerr)

    target = _find_setup(cam, setup)
    if not target:
        return error(f"No setup named '{setup}'. Setups: {', '.join(str(n) for n in _setup_names(cam))}.")

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

    op = target.operations.add(opin)        # MUTATION
    if not op:
        return error("operations.add returned no operation.")

    result = {
        "operation": safe(lambda: op.name),
        "setup": setup,
        "strategy": strategy,
        "generated": False,
        "note": "Operation created. " + ("" if generate else
                "Pass generate=true (or call cam_generate) to compute the toolpath."),
    }

    if generate:
        gerr = None
        try:
            cam.generateToolpath(op)         # async future; the op updates in place
        except Exception as e:
            gerr = str(e)
        result["generated"] = gerr is None
        result["has_toolpath"] = bool(safe(lambda: op.hasToolpath, False))
        result["toolpath_valid"] = bool(safe(lambda: op.isToolpathValid, False))
        if gerr:
            result["generate_error"] = gerr
            result["note"] = f"Operation created but toolpath generation errored: {gerr}"
        else:
            result["note"] = ("Operation created and toolpath generation started (async). Confirm with "
                              "cam_get(include=['operations']) (hasToolpath / isToolpathValid).")
    return ok(result)


TOOL_DESCRIPTION = (
    "CREATE a CAM milling operation in a setup (the 'apply an operation' half of CAM). 'setup' = the "
    "setup name; 'strategy' = face / adaptive / pocket2d / drill / bore / contour2d / ... (validated "
    "against the setup's compatible strategies). TOOL ref: 'tool_scope=document' + 'tool_index' (this "
    "doc's library - what cam_edit_tools scope='document' adds; no URL needed) OR 'tool_library_url' "
    "+ 'tool_index' (a shared library). 'generate' (default true) computes the toolpath. WRITES. Then "
    "cam_select_geometry targets the geometry. cam_create_setup makes the setup first."
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
            "description": "Generate the toolpath after creating (default true)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
