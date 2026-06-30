# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: manage CAM TOOLS across document / local / cloud / hub libraries.

  cam_tool(action=list|add|remove|edit|where_used, scope=document|local|cloud|hub, library=..., ...)

The cohesive tool-management surface. One action-dispatched verb covers the real interaction tasks an
agent needs:
  - list        -> the tools in the target library (index + key specs)
  - add         -> copy one or more tools IN by (library_url, index) reference (the cam_read_tool_library
                   handle); validates ALL refs before adding any
  - remove      -> remove one or more tools by index (removed high-to-low so indices stay valid)
  - edit        -> set named tool parameters by expression on one tool, then persist
  - where_used  -> which operations use a tool (DOCUMENT scope only)

Scope picks the library: 'document' = CAM.documentToolLibrary (this doc's tools, with where_used);
'local'/'cloud'/'hub' = the shared ToolLibrary at 'library' (name or url). Writes PERSIST — document via
DocumentToolLibrary.updateTool; shared via ToolLibraries.updateToolLibrary(url, lib). Hub is shared
TEAM data (writes affect others) and network-slow.

Grounded in adsk.cam (every path verified live):
  - CAM.documentToolLibrary -> DocumentToolLibrary(.count/.item/.add/.remove/.updateTool(tool)/
    .operationsByTool(tool) -> OperationVector)
  - ToolLibraries.toolLibraryAtURL(url) -> ToolLibrary(.count/.item/.add/.remove); persist with
    ToolLibraries.updateToolLibrary(url, lib)
  - Tool.parameters.itemByName(name).expression set/get
Handler runs on the main thread; WRITES CAM/library data (except list / where_used).
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()

_ACTIONS = ("list", "add", "remove", "edit", "where_used", "create_library")
_SCOPES = ("document", "local", "cloud", "hub")
_SHARED_LOCATIONS = {"local": "LocalLibraryLocation", "cloud": "CloudLibraryLocation",
                     "hub": "HubLibraryLocation"}


# ── target abstraction: a uniform view over document-lib vs shared-lib ───────

class _Target:
    """Uniform interface the handler drives, hiding document-vs-shared differences.
    persist() commits a shared library; document edits commit per-tool via update_tool()."""
    def __init__(self, lib, is_document, persist_fn=None, update_tool_fn=None, ops_fn=None):
        self._lib = lib
        self.is_document = is_document
        self._persist_fn = persist_fn
        self._update_tool_fn = update_tool_fn
        self._ops_fn = ops_fn

    @property
    def tools(self):
        return [safe(lambda i=i: self._lib.item(i)) for i in range(safe(lambda: self._lib.count, 0) or 0)]

    def add(self, tool):
        self._lib.add(tool)

    def remove(self, index):
        self._lib.remove(index)

    def update_tool(self, tool):
        if self._update_tool_fn:
            self._update_tool_fn(tool)

    def persist(self):
        if self._persist_fn:
            self._persist_fn()

    def operations_by_tool(self, tool):
        ops = self._ops_fn(tool) if self._ops_fn else None
        if ops is None:
            return []
        # OperationVector is index/len accessible, not a Python list
        out = []
        try:
            for i in range(len(ops)):
                out.append(safe(lambda i=i: ops[i].name))
        except Exception:
            for i in range(safe(lambda: ops.count, 0) or 0):
                out.append(safe(lambda i=i: ops.item(i).name))
        return out


def _get_cam():
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


def _tool_libraries():
    """The shared ToolLibraries — on CAMManager.get().libraryManager (NOT the document's CAM product,
    which has no libraryManager). Works without an open CAM job."""
    return safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)


def _shared_libraries(scope):
    """List (name, url) of the libraries at a shared scope, recursing folders (Hub/Cloud nest).
    Returns (entries, None) or (None, error). Patched in tests."""
    libs = _tool_libraries()
    if not libs:
        return None, "Tool libraries unavailable."
    loc = getattr(adsk.cam.LibraryLocations, _SHARED_LOCATIONS[scope])
    root = safe(lambda: libs.urlByLocation(loc))
    found = []

    def walk(url, depth):
        if depth > 6 or url is None:
            return
        for a in (safe(lambda: list(libs.childAssetURLs(url)), []) or []):
            found.append(a)
        for f in (safe(lambda: list(libs.childFolderURLs(url)), []) or []):
            walk(f, depth + 1)
    walk(root, 0)
    return [{"name": safe(lambda a=a: a.leafName), "url": safe(lambda a=a: a.toString())}
            for a in found], None


def _resolve_target(scope, library):
    """Return (_Target, None) for the scope, or (None, error). Patched in tests."""
    if scope == "document":
        cam, cerr = _get_cam()        # document scope needs an open CAM document
        if cerr:
            return None, cerr
        dtl = safe(lambda: cam.documentToolLibrary)
        if dtl is None:
            return None, "No document tool library."
        return _Target(dtl, is_document=True,
                       update_tool_fn=lambda t: dtl.updateTool(t),
                       ops_fn=lambda t: safe(lambda: dtl.operationsByTool(t))), None
    # shared library — no open document needed
    libs = _tool_libraries()
    if not libs:
        return None, "Tool libraries unavailable."
    loc = getattr(adsk.cam.LibraryLocations, _SHARED_LOCATIONS[scope])
    root = safe(lambda: libs.urlByLocation(loc))
    # collect libraries (recurse folders for Hub/Cloud)
    found = []

    def walk(url, depth):
        if depth > 6 or url is None:
            return
        for a in (safe(lambda: list(libs.childAssetURLs(url)), []) or []):
            found.append(a)
        for f in (safe(lambda: list(libs.childFolderURLs(url)), []) or []):
            walk(f, depth + 1)
    walk(root, 0)
    target = (library or "").strip()
    if not target:
        return None, f"Provide 'library' (name or url) for {scope} scope. Available: " \
                     f"{', '.join(safe(lambda a=a: a.leafName) for a in found)}."
    lib_url = next((a for a in found if safe(lambda a=a: a.toString()) == target), None) \
        or next((a for a in found if safe(lambda a=a: a.leafName) == target), None)
    if lib_url is None:
        avail = [safe(lambda a=a: a.leafName) for a in found]
        return None, f"No {scope} library '{target}'. Available: {', '.join(str(a) for a in avail)}."
    lib = safe(lambda: libs.toolLibraryAtURL(lib_url))
    if not lib:
        return None, f"Could not load {scope} library '{target}'."
    return _Target(lib, is_document=False,
                   persist_fn=lambda: libs.updateToolLibrary(lib_url, lib)), None


def _source_tool(library_url, index):
    """Fetch a Tool from a (library_url, index) reference. Patched in tests."""
    libs = safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)
    if not libs:
        return None, "Tool libraries unavailable."
    url = safe(lambda: adsk.core.URL.create(library_url))
    lib = safe(lambda: libs.toolLibraryAtURL(url)) if url else None
    if not lib:
        return None, f"Could not load source library '{library_url}'."
    n = safe(lambda: lib.count, 0) or 0
    if not (0 <= index < n):
        return None, f"tool_index {index} out of range for '{library_url}' ({n} tools)."
    t = safe(lambda: lib.item(index))
    return (t, None) if t is not None else (None, f"No tool at index {index}.")


# ── tool creation: clone a sample of a geometry type, build via JSON ─────────

import json as _json

_json_loads = _json.loads
_json_dumps = _json.dumps

# Fusion sample libraries that, together, hold one of every common geometry type.
_SAMPLE_LIBS = ("Milling Tools (Metric)", "Hole Making Tools (Metric)", "Cutting Tools (Metric)")
_HOLDERS_LIB = "Holders (Metric)"
_type_map_cache = None   # {tool_type: (library_url, index)} built once from the sample libs


def _tool_from_json(json_str):
    return adsk.cam.Tool.createFromJson(json_str)


def _fusion360_child(leaf_substr):
    """A Fusion360 sample-library URL whose leaf contains leaf_substr, or None."""
    libs = safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)
    if not libs:
        return None, None
    root = safe(lambda: libs.urlByLocation(adsk.cam.LibraryLocations.Fusion360LibraryLocation))
    for a in (safe(lambda: list(libs.childAssetURLs(root)), []) or []):
        if leaf_substr in (safe(lambda a=a: a.leafName) or ""):
            return libs, a
    return libs, None


def _build_type_map():
    """{tool_type -> (library_url, index)} from the sample libraries (built once, cached)."""
    global _type_map_cache
    if _type_map_cache is not None:
        return _type_map_cache
    out = {}
    libs = safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)
    if libs:
        root = safe(lambda: libs.urlByLocation(adsk.cam.LibraryLocations.Fusion360LibraryLocation))
        children = safe(lambda: list(libs.childAssetURLs(root)), []) or []
        for ln in _SAMPLE_LIBS:
            u = next((a for a in children if ln in (safe(lambda a=a: a.leafName) or "")), None)
            if not u:
                continue
            lib = safe(lambda: libs.toolLibraryAtURL(u))
            for i in range(safe(lambda: lib.count, 0) or 0):
                ty = safe(lambda lib=lib, i=i: lib.item(i).parameters.itemByName("tool_type").value.value)
                if ty and ty not in out:
                    out[ty] = (safe(lambda u=u: u.toString()), i)
    _type_map_cache = out
    return out


def _sample_for_type(tool_type):
    """A sample Tool of the given geometry type (e.g. 'drill', 'ball end mill'), or (None, error)."""
    tmap = _build_type_map()
    ref = tmap.get((tool_type or "").strip())
    if not ref:
        return None, (f"No sample tool of type '{tool_type}'. Available types: "
                      f"{', '.join(sorted(tmap.keys()))}.")
    return _source_tool(ref[0], ref[1])


def _holder_json(ref):
    """The holder JSON sub-dict for a {library_url, index} holder reference, or (None, error).
    A Holders-library item IS a holder doc (type='holder', has 'segments'); use it directly."""
    if not isinstance(ref, dict):
        return None, f"'holder' must be {{library_url, index}}; got {ref!r}."
    url, idx = ref.get("library_url"), ref.get("index")
    if url is None or idx is None:
        # convenience: no url given -> use the default Holders sample library
        if idx is None:
            return None, "'holder' needs an 'index' (and optionally a 'library_url')."
        libs, hu = _fusion360_child(_HOLDERS_LIB)
        if not hu:
            return None, "Default holders library not found; give an explicit 'library_url'."
        url = safe(lambda: hu.toString())
    htool, herr = _source_tool(url, idx)
    if herr:
        return None, herr
    hd = safe(lambda: _json_loads(htool.toJson()))
    if not isinstance(hd, dict):
        return None, "Could not read holder JSON."
    return (hd["holder"] if "holder" in hd else hd), None


# ── per-tool summary ─────────────────────────────────────────────────────────

def _tp(tool, name, default=None):
    p = safe(lambda: tool.parameters.itemByName(name))
    return safe(lambda: p.value.value, default) if p else default


def _tool_summary(tool, index):
    dia = _tp(tool, "tool_diameter")
    return {
        "index": index,
        "type": _tp(tool, "tool_type"),
        "diameter_mm": round(dia * 10.0, 4) if isinstance(dia, (int, float)) else None,
        "flutes": _tp(tool, "tool_numberOfFlutes"),
        "description": _tp(tool, "tool_description"),
    }


# ── actions ──────────────────────────────────────────────────────────────────

def _do_list(target, tool_type=""):
    tfilter = (tool_type or "").strip().lower()
    tools = []
    for i, t in enumerate(target.tools):
        summ = _tool_summary(t, i)
        if tfilter and tfilter not in str(summ.get("type") or "").lower():
            continue
        tools.append(summ)
    out = {"tool_count": len(tools), "tools": tools}
    if tfilter:
        out["filtered_by_type"] = tool_type
    return ok(out)


def _do_list_libraries(scope):
    entries, lerr = _shared_libraries(scope)
    if lerr:
        return error(lerr)
    return ok({"scope": scope, "library_count": len(entries), "libraries": entries,
               "note": "Pass 'library' = one of these (name or url) to list/manage its tools. Tool "
                       "references are (library_url, index)."})


def _build_entry(ref):
    """Build the Tool for one add entry, applying create/holder/preset/overrides. Returns (tool, None)
    or (None, error). An entry is a dict:
      {from_type: 'drill'}            -> clone a sample-library tool of that geometry type, OR
      {library_url, index}            -> copy that existing tool
      + optional 'description', 'diameter' overrides
      + optional 'holder': {library_url, index}  -> assign that holder
      + optional 'presets': [{name?, spindle_speed?, feed?}]  -> add presets after creation
    Building goes through the tool's JSON so the holder swap + description override are clean; the
    resulting Tool is created with _tool_from_json. (All steps verified live.)"""
    if not isinstance(ref, dict):
        return None, f"Each add_tools entry must be an object; got {ref!r}."

    # 1) get the SOURCE tool (by type-clone or by reference)
    if ref.get("from_type"):
        src, serr = _sample_for_type(ref["from_type"])
        if serr:
            return None, serr
    elif ref.get("library_url") is not None and ref.get("index") is not None:
        src, serr = _source_tool(ref.get("library_url"), ref.get("index"))
        if serr:
            return None, serr
    else:
        return None, (f"Entry {ref!r} needs 'from_type' (clone a sample of that type) or "
                      "'library_url'+'index' (copy an existing tool).")

    # 2) optional holder to ASSIGN (resolve before mutating)
    holder_json = None
    if ref.get("holder"):
        hd, herr = _holder_json(ref["holder"])
        if herr:
            return None, herr
        holder_json = hd

    # 3) build via JSON: clone source, apply overrides + holder
    d = safe(lambda: _json_loads(src.toJson()))
    if not isinstance(d, dict):
        return None, "Could not read the source tool's JSON."
    if ref.get("description"):
        d["description"] = str(ref["description"])
    if holder_json is not None:
        d["holder"] = holder_json
    tool = safe(lambda: _tool_from_json(_json_dumps(d)))
    if tool is None:
        return None, "Could not create the tool from JSON."
    # diameter override (after creation, on the param)
    if ref.get("diameter") is not None:
        p = safe(lambda: tool.parameters.itemByName("tool_diameter"))
        if p is not None:
            safe(lambda: setattr(p, "expression", str(ref["diameter"])))

    # 4) presets
    for ps in (ref.get("presets") or []):
        preset = safe(lambda: tool.presets.add())
        if preset is None:
            continue
        if ps.get("spindle_speed") is not None:
            sp = safe(lambda: preset.parameters.itemByName("tool_spindleSpeed"))
            if sp is not None:
                safe(lambda v=ps["spindle_speed"]: setattr(sp, "expression", str(v)))
        if ps.get("feed") is not None:
            fp = safe(lambda: preset.parameters.itemByName("tool_feedCutting"))
            if fp is not None:
                safe(lambda v=ps["feed"]: setattr(fp, "expression", str(v)))
    return tool, None


def _do_add(target, add_tools):
    if not add_tools:
        return error("Provide 'add_tools' — entries to add. Each: {from_type:'drill'} (create from a "
                     "sample of that type) or {library_url, index} (copy an existing tool); optional "
                     "'description'/'diameter' overrides, 'holder':{library_url,index}, 'presets':[...].")
    # build ALL entries before adding any (no partial write on an error)
    built = []
    for ref in add_tools:
        t, terr = _build_entry(ref)
        if terr:
            return error(terr)
        built.append(t)
    for t in built:
        target.add(t)
    if not target.is_document:
        target.persist()
    return ok({"added": len(built), "tool_count": len(target.tools),
               "note": "Tools added and persisted." if not target.is_document
                       else "Tools added to the document library."})


def _do_remove(target, indices):
    if not indices:
        return error("Provide 'remove_indices' — the tool indices to remove.")
    n = len(target.tools)
    bad = [i for i in indices if not (0 <= i < n)]
    if bad:
        return error(f"Index/indices out of range (library has {n} tools): {', '.join(map(str, bad))}.")
    # remove high-to-low so earlier indices stay valid
    for i in sorted(set(indices), reverse=True):
        target.remove(i)
    if not target.is_document:
        target.persist()
    return ok({"removed": len(set(indices)), "tool_count": len(target.tools)})


def _do_edit(target, tool_index, parameters):
    tools = target.tools
    if tool_index is None or not (0 <= tool_index < len(tools)):
        return error(f"Provide a valid 'tool' index (0..{len(tools) - 1}).")
    if not parameters:
        return error("Provide 'parameters' {name: expression} to set on the tool.")
    tool = tools[tool_index]
    params = safe(lambda: tool.parameters)
    # validate ALL parameter names before applying any
    resolved = {}
    missing = []
    for name in parameters:
        p = safe(lambda name=name: params.itemByName(name)) if params else None
        (resolved.__setitem__(name, p) if p is not None else missing.append(name))
    if missing:
        return error(f"Tool has no parameter(s): {', '.join(missing)}. (Read the tool's parameters first.)")
    changed = []
    for name, expr in parameters.items():
        p = resolved[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = str(expr)
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}': {e}. "
                         f"(Applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        changed.append({"name": name, "before": before, "after": safe(lambda p=p: p.expression)})
    # persist
    if target.is_document:
        target.update_tool(tool)
    else:
        target.persist()
    return ok({"edited": len(changed), "tool": tool_index, "changed": changed,
               "note": "Tool edited and persisted."})


def _do_where_used(target, tool_index):
    if not target.is_document:
        return error("'where_used' is only available for the document library (scope='document') — a "
                     "shared library has no operations.")
    tools = target.tools
    if tool_index is None or not (0 <= tool_index < len(tools)):
        return error(f"Provide a valid 'tool' index (0..{len(tools) - 1}).")
    tool = tools[tool_index]
    ops = target.operations_by_tool(tool)
    return ok({"tool": tool_index, "description": _tp(tool, "tool_description"),
               "operation_count": len(ops), "operations": ops,
               "note": "Operations that use this tool." if ops else "This tool is not used by any operation."})


# friendly scope -> LibraryLocations attr for creating a new library (document can't host a new library).
_CREATE_LOCATIONS = {"local": "LocalLibraryLocation", "cloud": "CloudLibraryLocation",
                     "hub": "HubLibraryLocation"}


def _empty_library():
    return adsk.cam.ToolLibrary.createEmpty()


def _do_create_library(scope, name, seed_tools):
    """Create + persist a NEW tool library at a shared scope (Local/Cloud/Hub). Fusion360 is read-only;
    document scope can't host a new library. Seeds validated before the persistent write."""
    if scope == "document":
        return error("Cannot create a library in the document scope. Use scope=local/cloud/hub.")
    if scope not in _CREATE_LOCATIONS:
        return error(f"Cannot create a library at scope '{scope}'. Use local / cloud / hub.")
    name = (name or "").strip()
    if not name:
        return error("Provide 'library' as the new library's name (create_library).")
    libs = _tool_libraries()
    if not libs:
        return error("Tool libraries unavailable.")
    root = safe(lambda: libs.urlByLocation(getattr(adsk.cam.LibraryLocations, _CREATE_LOCATIONS[scope])))
    if not root:
        return error(f"Could not resolve the '{scope}' library root.")
    # Hub can't import at the bare hub:// root — descend to its team folder.
    if scope == "hub":
        child = safe(lambda: list(libs.childFolderURLs(root)), []) or []
        if not child:
            return error("No hub folder to create the library in.")
        root = child[0]
    # resolve seed tools BEFORE the persistent write
    resolved = []
    for ref in (seed_tools or []):
        if not isinstance(ref, dict):
            return error(f"Each seed entry must be {{library_url, index}}; got {ref!r}.")
        t, terr = _source_tool(ref.get("library_url"), ref.get("index"))
        if terr:
            return error(terr)
        resolved.append(t)
    lib = safe(lambda: _empty_library())
    if not lib:
        return error("Could not create an empty tool library.")
    for t in resolved:
        safe(lambda t=t: lib.add(t))
    try:
        new_url = libs.importToolLibrary(lib, root, name)
    except Exception as e:
        hint = (" Hub team libraries use a different write path importToolLibrary doesn't satisfy — "
                "create Hub libraries in the UI." if scope == "hub" else "")
        return error(f"Creating library '{name}' at {scope} failed: {e}.{hint}")
    if not new_url:
        return error(f"Creating library '{name}' at {scope} returned no URL.")
    return ok({"created_library": name, "scope": scope, "url": safe(lambda: new_url.toString()),
               "tool_count": safe(lambda: lib.count, len(resolved)),
               "note": "Library created and persisted. List it with action='list'. (Local=disk, "
                       "Cloud/Hub=your Autodesk account; a duplicate name gets a numeric suffix.)"})


def handler(action: str = "list", scope: str = "document", library: str = "",
            add_tools=None, remove_indices=None, tool=None, parameters=None,
            tool_type: str = "") -> dict:
    """Manage CAM tools + libraries. action: list/add/remove/edit/where_used. scope: document/local/
    cloud/hub. library: shared-library name/url — OMIT with action='list' (shared scope) to LIST the
    libraries. add_tools: [{library_url,index}]. remove_indices: [int]. tool: a tool index
    (edit/where_used). parameters: {name: expression} (edit). tool_type: list filter. WRITES (except
    list/where_used)."""
    action = (action or "list").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use one of: {', '.join(_ACTIONS)}.")
    scope = (scope or "document").strip().lower()
    if scope not in _SCOPES:
        return error(f"Unknown scope '{scope}'. Use one of: {', '.join(_SCOPES)}.")

    # create_library: the target doesn't exist yet — 'library' is the NEW name. Dispatch before resolve.
    if action == "create_library":
        return _do_create_library(scope, library, add_tools)

    # list with a shared scope and NO library -> list the libraries at that location (subsumes the old
    # cam_read_tool_library library-listing). document scope always has exactly one library.
    if action == "list" and scope != "document" and not (library or "").strip():
        return _do_list_libraries(scope)

    target, terr = _resolve_target(scope, library)
    if terr:
        return error(terr)

    if action == "list":
        return _do_list(target, tool_type)
    if action == "add":
        return _do_add(target, add_tools or [])
    if action == "remove":
        return _do_remove(target, remove_indices or [])
    if action == "edit":
        return _do_edit(target, tool, parameters)
    if action == "where_used":
        return _do_where_used(target, tool)
    return error(f"Unhandled action '{action}'.")


TOOL_DESCRIPTION = (
    "Read & manage CAM TOOL LIBRARIES + their tools. 'scope': document (this doc's tools) / local / cloud "
    "/ hub. 'action': 'list' — with a shared scope and NO 'library', lists the libraries there; otherwise "
    "lists that library's tools (each with a (library_url,index) reference; optional 'tool_type' filter). "
    "'add' (each 'add_tools' entry CREATES a tool: {from_type:'drill'} clones a sample of that geometry "
    "type, or {library_url,index} copies an existing one; + optional 'description'/'diameter', "
    "'holder':{library_url,index}, 'presets':[{spindle_speed,feed}]) / 'remove' ('remove_indices'=[int]) / "
    "'edit' (set 'parameters'={name:expression} on the 'tool' index) / 'where_used' (operations using the "
    "'tool' index — document scope only) / 'create_library' (new library named 'library' at scope "
    "local/cloud/hub, optionally seeded with 'add_tools'). For shared scopes give 'library' (name or url). "
    "WRITES persist (Hub is shared TEAM data; Hub reads/writes are network-slow). 'list'/'where_used' read-only."
)

tool = (
    Tool.create_simple(name="cam_tool_library", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS),
            "description": "list / add / remove / edit / where_used."})
    .add_input_property("scope", {"type": "string", "enum": list(_SCOPES),
            "description": "document / local / cloud / hub."})
    .add_input_property("library", {"type": "string", "description": "Shared-library name or url (not for document scope)."})
    .add_input_property("add_tools", {"type": "array",
            "items": {"type": "object", "properties": {
                "from_type": {"type": "string"}, "library_url": {"type": "string"}, "index": {"type": "integer"},
                "description": {"type": "string"}, "diameter": {"type": "string"},
                "holder": {"type": "object", "properties": {"library_url": {"type": "string"}, "index": {"type": "integer"}}},
                "presets": {"type": "array", "items": {"type": "object", "properties": {
                    "spindle_speed": {"type": "number"}, "feed": {"type": "number"}}}}}},
            "description": "Tools to add/create. Each: {from_type:'drill'} (clone a sample of that type) OR {library_url,index} (copy); + optional description/diameter, holder:{library_url,index}, presets:[{spindle_speed,feed}]."})
    .add_input_property("remove_indices", {"type": "array", "items": {"type": "integer"},
            "description": "Tool indices to remove."})
    .add_input_property("tool", {"type": "integer", "description": "Tool index (edit / where_used)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Tool parameters to set (edit): {name: expression}."})
    .add_input_property("tool_type", {"type": "string",
            "description": "Filter for list (substring on tool type, e.g. 'ball', 'drill')."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
