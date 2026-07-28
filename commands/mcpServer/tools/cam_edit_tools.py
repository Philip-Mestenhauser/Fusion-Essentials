# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Manage CAM tools across the document / local / cloud / hub tool libraries: list, add, remove, edit
parameters, find where a tool is used, or create a new shared library. Hub libraries can't be created
via the API (importToolLibrary fails there) - create those in the UI."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, expression_error

app = adsk.core.Application.get()

_ACTIONS = ("list", "list_types", "parameters", "add", "remove", "edit", "where_used", "create_library")
_SCOPES = ("document", "local", "cloud", "hub")
_SHARED_LOCATIONS = {"local": "LocalLibraryLocation", "cloud": "CloudLibraryLocation",
                     "hub": "HubLibraryLocation"}


# ── target abstraction: a uniform view over document-lib vs shared-lib ───────

class _Target:
    """Uniform interface the handler drives, hiding document-vs-shared differences.
    persist() commits a shared library; document edits commit per-tool via update_tool()."""
    def __init__(self, lib, is_document, persist_fn=None, update_tool_fn=None, ops_fn=None,
                 refetch_count_fn=None, reread_param_fn=None):
        self._lib = lib
        self.is_document = is_document
        self._persist_fn = persist_fn
        self._update_tool_fn = update_tool_fn
        self._ops_fn = ops_fn
        self._refetch_count_fn = refetch_count_fn
        self._reread_param_fn = reread_param_fn

    def persisted_count(self):
        """The tool count re-read FRESH from the persisted url (None when unavailable) - the proof
        a persist() actually landed; updateToolLibrary returning true is not."""
        return self._refetch_count_fn() if self._refetch_count_fn else None

    def reread_param(self, index, name):
        """The expression of one parameter re-read FRESH from the persisted library (None when
        unavailable) - the proof an edit() actually stored; updateTool/updateToolLibrary returning
        true is not."""
        return self._reread_param_fn(index, name) if self._reread_param_fn else None

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




def _tool_libraries():
    """The shared ToolLibraries - on CAMManager.get().libraryManager (NOT the document's CAM product,
    which has no libraryManager). Works without an open CAM job."""
    return safe(lambda: adsk.cam.CAMManager.get().libraryManager.toolLibraries)


def _collect_library_urls(libs, root):
    """Every tool-library asset URL under a shared root, recursing folders (Hub/Cloud nest), bounded
    to depth 6. The ONE library-folder walk both _shared_libraries and _resolve_target target."""
    found = []

    def walk(url, depth):
        if depth > 6 or url is None:
            return
        for a in (safe(lambda: list(libs.childAssetURLs(url)), []) or []):
            found.append(a)
        for f in (safe(lambda: list(libs.childFolderURLs(url)), []) or []):
            walk(f, depth + 1)
    walk(root, 0)
    return found


def _shared_libraries(scope):
    """List (name, url) of the libraries at a shared scope, recursing folders (Hub/Cloud nest).
    Returns (entries, None) or (None, error). Patched in tests."""
    libs = _tool_libraries()
    if not libs:
        return None, "Tool libraries unavailable."
    loc = getattr(adsk.cam.LibraryLocations, _SHARED_LOCATIONS[scope])
    root = safe(lambda: libs.urlByLocation(loc))
    found = _collect_library_urls(libs, root)
    return [{"name": safe(lambda a=a: a.leafName), "url": safe(lambda a=a: a.toString())}
            for a in found], None


def _resolve_target(scope, library):
    """Return (_Target, None) for the scope, or (None, error). Patched in tests."""
    if scope == "document":
        cam, cerr = get_cam()        # document scope needs an open CAM document
        if cerr:
            return None, cerr
        dtl = safe(lambda: cam.documentToolLibrary)
        if dtl is None:
            return None, "No document tool library."
        return _Target(dtl, is_document=True,
                       update_tool_fn=lambda t: dtl.updateTool(t),
                       ops_fn=lambda t: safe(lambda: dtl.operationsByTool(t)),
                       reread_param_fn=lambda idx, nm: safe(
                           lambda: dtl.item(idx).parameters.itemByName(nm).expression)), None
    # shared library - no open document needed
    libs = _tool_libraries()
    if not libs:
        return None, "Tool libraries unavailable."
    loc = getattr(adsk.cam.LibraryLocations, _SHARED_LOCATIONS[scope])
    root = safe(lambda: libs.urlByLocation(loc))
    # collect libraries (recurse folders for Hub/Cloud) via the shared walk
    found = _collect_library_urls(libs, root)
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
                   persist_fn=lambda: libs.updateToolLibrary(lib_url, lib),
                   refetch_count_fn=lambda: safe(lambda: libs.toolLibraryAtURL(lib_url).count),
                   reread_param_fn=lambda idx, nm: safe(
                       lambda: libs.toolLibraryAtURL(lib_url).item(idx).parameters.itemByName(nm).expression)), None


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


def _quote(text):
    """Quote a string as a Fusion parameter expression literal - the form tool_description's own
    string parameter is stored in. Used for tool_productId/tool_vendor: createFromJson's JSON schema
    silently drops those keys (verified live), so they are applied as expressions AFTER creation
    instead (see _build_entry)."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


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


# ── tool number: auto-assigned on add so multiple adds don't collide ──────────
# A cloned sample keeps the sample's tool_number, so two adds land at the same number and cam_post
# refuses ("Different tools have the same tool number"). On add we hand each new tool the next FREE
# number. tool_number is an expression-settable integer parameter (cam_edit_tools' own edit path sets
# it via .expression), read back via .value.value.
_P_TOOL_NUMBER = "tool_number"


def _read_tool_number(tool):
    """The tool's assigned tool_number as an int, or None if the parameter is absent/unreadable."""
    p = safe(lambda: tool.parameters.itemByName(_P_TOOL_NUMBER))
    if p is None:
        return None
    v = safe(lambda: p.value.value)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _set_tool_number(tool, number):
    """Set a tool's tool_number and read it back. (assigned_int, None) on success, (None, error) if
    the parameter is missing or the value did not land."""
    p = safe(lambda: tool.parameters.itemByName(_P_TOOL_NUMBER))
    if p is None:
        return None, "The tool has no 'tool_number' parameter - a free number could not be assigned."
    try:
        p.expression = str(int(number))
    except Exception as e:
        return None, f"Could not set tool_number to {number}: {e}."
    landed = _read_tool_number(tool)
    if landed != int(number):
        return None, (f"Set tool_number to {number} but it read back {landed!r} - "
                      "the number did not land.")
    return int(number), None


# ── per-tool summary ─────────────────────────────────────────────────────────

def _tp(tool, name, default=None):
    p = safe(lambda: tool.parameters.itemByName(name))
    return safe(lambda: p.value.value, default) if p else default


def _json_scalar(v):
    """A parameter's evaluated value coerced to a JSON-safe scalar; a non-scalar value type is
    str()'d so it is still reported, never dropped."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return safe(lambda: str(v))


def _tool_summary(tool, index):
    from ._cam_common import tool_holder
    dia = _tp(tool, "tool_diameter")
    summ = {
        "index": index,
        "number": _read_tool_number(tool),   # the tool NUMBER (auto-assigned on add; cam_post keys on it)
        "type": _tp(tool, "tool_type"),
        "diameter_mm": round(dia * 10.0, 4) if isinstance(dia, (int, float)) else None,
        "flutes": _tp(tool, "tool_numberOfFlutes"),
        "description": _tp(tool, "tool_description"),
    }
    # The CUTTING tool's OWN product identity (tool_productId/tool_vendor) - distinct from the holder's
    # product_id/vendor below. The add path sets and verifies these, so this list read confirms them.
    product_id = _tp(tool, "tool_productId")
    vendor = _tp(tool, "tool_vendor")
    if product_id:
        summ["tool_product_id"] = product_id
    if vendor:
        summ["tool_vendor"] = vendor
    holder = tool_holder(tool)   # assigned holder identity - shown only when the tool carries one
    if holder:
        summ["holder"] = holder
    return summ


# ── actions ──────────────────────────────────────────────────────────────────

def _do_list(target, tool_type=""):
    tfilter = (tool_type or "").strip().lower()
    tools = []
    for i, t in enumerate(target.tools):
        summ = _tool_summary(t, i)
        if tfilter and tfilter not in str(summ.get("type") or "").lower():
            continue
        tools.append(summ)
    out = {"tool_count": len(tools), "tools": tools,
           "note": "Summary rows only (diameter/flutes/type/description/number/product identity). "
                   "For a tool's FULL parameter list - every dimension by name/expression/value - "
                   "call action='parameters' with that tool's index."}
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


def _do_list_types():
    """The tool-type vocabulary _build_type_map() discovers by walking the bundled sample libraries -
    the same map action='add' resolves 'from_type' against. No document/CAM product/scope/library
    needed (replaces harvesting the vocabulary off a deliberately-failing add probe)."""
    tmap = _build_type_map()
    if not tmap:
        return error("Could not read the sample tool libraries - tool types are unavailable.")
    return ok({"type_count": len(tmap), "types": sorted(tmap.keys()),
               "note": "Pass one of these as add_tools[].from_type to clone a sample of that type."})


# The preset 'feed' parameter name varies by tool CLASS: a mill preset carries 'tool_feedCutting', but a
# drill / hole-making preset does NOT (it exposes plunge/drilling feeds instead). So the {feed} preset
# shape maps to the FIRST of these the preset actually carries; the error names what IS there otherwise.
_FEED_PARAM_CANDIDATES = ("tool_feedCutting", "tool_feedPlunge", "tool_feedRamp",
                          "tool_feedRetract", "tool_feedEntry", "tool_feedTransition")


def _preset_feed_param(preset):
    """The preset ModelParameter that the 'feed' value should drive, or (None, available_feed_names).
    Tries the known cutting/plunge feed names in order (so a mill keeps using tool_feedCutting and a
    drill falls through to its plunge feed); on a miss it returns the feed-named params the preset DOES
    have, so the error can name the right drill path instead of asserting a mill-only parameter."""
    params = safe(lambda: preset.parameters)
    if params is None:
        return None, []
    for nm in _FEED_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: params.itemByName(nm))
        if p is not None:
            return p, None
    feed_names = []
    for i in range(safe(lambda: params.count, 0) or 0):
        nm = safe(lambda i=i: params.item(i).name) or ""
        if "feed" in nm.lower():
            feed_names.append(nm)
    return None, feed_names


def _build_entry(ref):
    """Build the Tool for one add entry, applying create/holder/preset/overrides. Returns (tool, None)
    or (None, error). An entry is a dict:
      {from_type: 'drill'}            -> clone a sample-library tool of that geometry type, OR
      {library_url, index}            -> copy that existing tool
      + optional 'description', 'diameter', 'product_id', 'vendor' overrides
      + optional 'holder': {library_url, index}  -> assign that holder
      + optional 'presets': [{name?, spindle_speed?, feed?}]  -> add presets after creation
    Building goes through the tool's JSON so the holder swap + description override are clean; the
    resulting Tool is created with _tool_from_json. product_id/vendor are NOT part of that JSON
    schema (createFromJson silently drops them - verified live) so they are applied as quoted-string
    expressions on tool_productId/tool_vendor AFTER creation, then read back. (All steps verified
    live.)"""
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
    # diameter override (after creation, on the param). A missing parameter means the requested
    # override cannot apply - error instead of adding the tool without it.
    if ref.get("diameter") is not None:
        p = safe(lambda: tool.parameters.itemByName("tool_diameter"))
        if p is None:
            return None, "The tool has no 'tool_diameter' parameter - the requested diameter override cannot apply."
        p.expression = str(ref["diameter"])

    # 3b) product_id / vendor: real tool parameters (tool_productId/tool_vendor), but NOT part of
    # createFromJson's JSON schema - those keys are silently dropped there (the holder JSON's own
    # 'product-id'/'vendor' keys are a different, unrelated concept; verified live). Apply
    # as quoted-string expressions AFTER creation - the same form tool_description's own string value
    # is stored in - then read the landed value back; a mismatch is an error, never a silent gap.
    for field, pname in (("product_id", "tool_productId"), ("vendor", "tool_vendor")):
        val = ref.get(field)
        if val is None:
            continue
        val = str(val)
        p = safe(lambda pname=pname: tool.parameters.itemByName(pname))
        if p is None:
            return None, f"The tool has no '{pname}' parameter - the requested {field} cannot apply."
        try:
            p.expression = _quote(val)
        except Exception as e:
            return None, f"Could not set {pname} = {val!r}: {e}."
        eerr, _ = expression_error(p)
        if eerr:
            return None, f"Set {pname} but it failed to evaluate: {eerr}."
        landed = safe(lambda p=p: p.value.value)
        if landed != val:
            return None, (f"Set {pname}'s expression but it read back {landed!r} instead of "
                          f"{val!r} - the {field} did not land.")

    # 4) presets - same rule: a preset that cannot be created or populated is an error, not a skip
    for ps in (ref.get("presets") or []):
        preset = safe(lambda: tool.presets.add())
        if preset is None:
            return None, "Could not add a preset to the tool - the requested presets were not applied."
        if ps.get("spindle_speed") is not None:
            sp = safe(lambda: preset.parameters.itemByName("tool_spindleSpeed"))
            if sp is None:
                return None, "The preset has no 'tool_spindleSpeed' parameter - the requested spindle_speed cannot apply."
            sp.expression = str(ps["spindle_speed"])
        if ps.get("feed") is not None:
            fp, feed_avail = _preset_feed_param(preset)
            if fp is None:
                avail = ", ".join(feed_avail) if feed_avail else "(no feed parameters)"
                return None, ("This tool's preset has no mill cutting-feed parameter (tool_feedCutting) - "
                              "a drill/hole-making preset uses a different feed. Feed parameters on this "
                              f"preset: {avail}. Set the right one via action='edit'.")
            fp.expression = str(ps["feed"])
    return tool, None


def _do_add(target, add_tools):
    if not add_tools:
        return error("Provide 'add_tools' - entries to add. Each: {from_type:'drill'} (create from a "
                     "sample of that type) or {library_url, index} (copy an existing tool); optional "
                     "'description'/'diameter'/'product_id'/'vendor' overrides, "
                     "'holder':{library_url,index}, 'presets':[...].")
    # build ALL entries before adding any (no partial write on an error)
    built = []
    for ref in add_tools:
        t, terr = _build_entry(ref)
        if terr:
            return error(terr)
        built.append(t)
    # Auto-assign a FREE tool_number to each new tool. A cloned sample keeps the sample's number, so
    # two adds would collide and cam_post refuses duplicate tool numbers; hand out the next free one
    # (skipping every number already in the library, and each one just assigned in this call).
    used = {n for n in (_read_tool_number(t) for t in target.tools) if n is not None}
    assigned = []
    nxt = 1
    for t in built:
        while nxt in used:
            nxt += 1
        num, nerr = _set_tool_number(t, nxt)
        if nerr:
            return error(nerr)
        used.add(num)
        assigned.append(num)
    for t in built:
        target.add(t)
    if not target.is_document:
        target.persist()
        got = target.persisted_count()
        if got is not None and got != len(target.tools):
            return error(f"updateToolLibrary reported success but the library re-read from its url "
                         f"holds {got} tool(s), not {len(target.tools)} - the persist did not land.")
    # Honesty read-back, scoped: this re-reads each IN-MEMORY tool object's number after the
    # add/persist - it catches an assignment that did not stick on the object, and the persisted
    # COUNT is verified from the url above, but a persist-side renumber of an individual tool
    # (never observed live) would pass; only cam_post's duplicate-number refusal would catch it.
    landed = [_read_tool_number(t) for t in built]
    if landed != assigned:
        return error(f"Auto-assigned tool numbers {assigned} but after the add they read back "
                     f"{landed} - the tool-number assignment did not persist.")
    return ok({"added": len(built), "tool_count": len(target.tools),
               "assigned_tool_numbers": assigned,
               "note": (("Tools added and persisted. " if not target.is_document
                         else "Tools added to the document library. ")
                        + f"Auto-assigned free tool number(s) {assigned} (next free per tool, so "
                        "multiple adds do not collide - cam_post refuses duplicate tool numbers).")})


def _do_remove(target, indices):
    if not indices:
        return error("Provide 'remove_indices' - the tool indices to remove.")
    n = len(target.tools)
    bad = [i for i in indices if not (0 <= i < n)]
    if bad:
        return error(f"Index/indices out of range (library has {n} tools): {', '.join(map(str, bad))}.")
    # remove high-to-low so earlier indices stay valid
    for i in sorted(set(indices), reverse=True):
        target.remove(i)
    if not target.is_document:
        target.persist()
        got = target.persisted_count()
        if got is not None and got != len(target.tools):
            return error(f"updateToolLibrary reported success but the library re-read from its url "
                         f"holds {got} tool(s), not {len(target.tools)} - the persist did not land.")
    return ok({"removed": len(set(indices)), "tool_count": len(target.tools)})


def _formula_source(params, name, before_expr):
    """If `before_expr` (a parameter's CURRENT expression, before this edit lands) is exactly another
    parameter's NAME on the same tool, the value is formula-derived - it tracks that other parameter
    (e.g. tool_shoulderLength's expression is the literal string 'tool_fluteLength') rather than
    holding an independent literal. Returns the referenced name, or None for an ordinary literal/
    numeric/quoted expression. Verified live: overwriting a formula-derived parameter
    works syntactically but silently breaks the tool's own internal relationship, so the caller warns
    instead of editing quietly."""
    ref = (before_expr or "").strip()
    if not ref or ref == name:
        return None
    return ref if safe(lambda: params.itemByName(ref)) is not None else None


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
    warnings = []
    for name, expr in parameters.items():
        p = resolved[name]
        before = safe(lambda p=p: p.expression)
        src = _formula_source(params, name, before)
        if src:
            warnings.append(f"'{name}' was formula-derived (expression was '{src}', tracking that "
                            f"parameter) - this edit overwrites that internal relationship; the tool "
                            f"no longer keeps '{name}' equal to '{src}'.")
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
    # Persist read-back (honesty contract): updateTool/updateToolLibrary returning is NOT proof the
    # edit stored. An edit changes no tool COUNT (so the add/remove count gate can't cover it) - so
    # re-fetch the tool from the library and confirm ONE edited expression actually landed.
    check = changed[0]
    stored = target.reread_param(tool_index, check["name"])
    if stored is not None and str(stored) != str(check["after"]):
        return error(f"Edited '{check['name']}' to '{check['after']}' but the tool re-read from the "
                     f"library holds '{stored}' - the edit did not persist.")
    out = {"edited": len(changed), "tool": tool_index, "changed": changed,
           "note": "Tool edited and persisted."}
    if warnings:
        out["warnings"] = warnings
    return ok(out)


def _do_where_used(target, tool_index):
    if not target.is_document:
        return error("'where_used' is only available for the document library (scope='document') - a "
                     "shared library has no operations.")
    tools = target.tools
    if tool_index is None or not (0 <= tool_index < len(tools)):
        return error(f"Provide a valid 'tool' index (0..{len(tools) - 1}).")
    tool = tools[tool_index]
    ops = target.operations_by_tool(tool)
    return ok({"tool": tool_index, "description": _tp(tool, "tool_description"),
               "operation_count": len(ops), "operations": ops,
               "note": "Operations that use this tool." if ops else "This tool is not used by any operation."})


def _do_parameters(target, tool_index):
    """The full parameter list of ONE tool - the deeper read the list summary points to. Reports each
    parameter's name, expression, and evaluated value where readable; only what the API exposes, no
    guessed names. 'formula_source' flags a parameter whose expression IS another parameter's name
    (it tracks that parameter rather than holding a literal; an edit overwrites that relationship)."""
    tools = target.tools
    if tool_index is None or not (0 <= tool_index < len(tools)):
        return error(f"Provide a valid 'tool' index (0..{len(tools) - 1}).")
    tool = tools[tool_index]
    params = safe(lambda: tool.parameters)
    n = safe(lambda: params.count, 0) or 0 if params is not None else 0
    rows = []
    for i in range(n):
        p = safe(lambda i=i: params.item(i))
        if p is None:
            continue
        name = safe(lambda p=p: p.name)
        expr = safe(lambda p=p: p.expression)
        row = {"name": name, "expression": expr,
               "value": _json_scalar(safe(lambda p=p: p.value.value))}
        src = _formula_source(params, name, expr)
        if src:
            row["formula_source"] = src
        rows.append(row)
    return ok({"tool": tool_index, "description": _tp(tool, "tool_description"),
               "parameter_count": len(rows), "parameters": rows,
               "note": "Every parameter's name/expression/value (value is null where unreadable). "
                       "'formula_source' marks a parameter tracking another (editing it overwrites "
                       "that relationship). Set one with action='edit'."})


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
    # Hub can't import at the bare hub:// root - descend to its team folder.
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
        hint = (" Hub team libraries use a different write path importToolLibrary doesn't satisfy - "
                "create Hub libraries in the UI." if scope == "hub" else "")
        return error(f"Creating library '{name}' at {scope} failed: {e}.{hint}")
    if not new_url:
        return error(f"Creating library '{name}' at {scope} returned no URL.")
    if safe(lambda: libs.toolLibraryAtURL(new_url)) is None:
        return error(f"importToolLibrary returned a URL but no library loads back from it - the "
                     "create did not land.")
    return ok({"created_library": name, "scope": scope, "url": safe(lambda: new_url.toString()),
               "tool_count": safe(lambda: lib.count, len(resolved)),
               "note": "Library created and persisted. List it with action='list'. (Local=disk, "
                       "Cloud/Hub=your Autodesk account; a duplicate name gets a numeric suffix.)"})


def read_library(scope: str = "document", library: str = "", tool_type: str = "") -> dict:
    """The READ-ONLY library listing, shared with cam_get(include=['library']). Lists the tools in the
    target library (or, for a shared scope with no 'library', the libraries at that location). The write
    actions (add/remove/edit) stay on the cam_edit_tools tool - this is just the read half."""
    scope = (scope or "document").strip().lower()
    if scope not in _SCOPES:
        return error(f"Unknown scope '{scope}'. Use one of: {', '.join(_SCOPES)}.")
    if scope != "document" and not (library or "").strip():
        return _do_list_libraries(scope)
    target, terr = _resolve_target(scope, library)
    if terr:
        return error(terr)
    return _do_list(target, tool_type)


def handler(action: str = "list", scope: str = "document", library: str = "",
            add_tools=None, remove_indices=None, tool=None, parameters=None,
            tool_type: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    action = (action or "list").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use one of: {', '.join(_ACTIONS)}.")
    scope = (scope or "document").strip().lower()
    if scope not in _SCOPES:
        return error(f"Unknown scope '{scope}'. Use one of: {', '.join(_SCOPES)}.")

    # create_library: the target doesn't exist yet - 'library' is the NEW name. Dispatch before resolve.
    if action == "create_library":
        return _do_create_library(scope, library, add_tools)

    # list is the READ half - one implementation, also surfaced as cam_get(include=['library']).
    if action == "list":
        return read_library(scope, library, tool_type)
    # list_types needs no scope/library/document - _build_type_map reads the bundled sample libraries
    # directly (dispatched here, before _resolve_target, same as create_library above).
    if action == "list_types":
        return _do_list_types()

    target, terr = _resolve_target(scope, library)
    if terr:
        return error(terr)

    if action == "add":
        return _do_add(target, add_tools or [])
    if action == "remove":
        return _do_remove(target, remove_indices or [])
    if action == "edit":
        return _do_edit(target, tool, parameters)
    if action == "where_used":
        return _do_where_used(target, tool)
    if action == "parameters":
        return _do_parameters(target, tool)
    return error(f"Unhandled action '{action}'.")


TOOL_DESCRIPTION = (
    "Read & manage CAM TOOL LIBRARIES + their tools. 'scope': document / local / cloud / hub. "
    "'action': list | list_types | parameters | add | remove | edit | where_used | create_library. "
    "'list' with a shared scope and NO 'library' lists the libraries there, else that library's tools "
    "(each carries a (library_url,index) reference); 'list_types' lists the from_type vocabulary; "
    "'parameters' reads one tool's FULL parameter list (name/expression/value, flags formula-derived). "
    "WRITES persist; Hub is shared TEAM data and network-slow. 'where_used' is document-scope only. "
    "list/list_types/parameters/"
    "where_used read-only. 'add' auto-assigns each new tool a free tool number (in assigned_tool_numbers; "
    "cam_post refuses duplicates)."
)

tool = (
    Tool.create_simple(name="cam_edit_tools", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS),
            "description": "One of the enum values; each is described in the tool description."})
    .add_input_property("scope", {"type": "string", "enum": list(_SCOPES),
            "description": "document / local / cloud / hub."})
    .add_input_property("library", {"type": "string", "description": "Shared-library name or url (not for document scope)."})
    .add_input_property("add_tools", {"type": "array",
            "items": {"type": "object", "properties": {
                "from_type": {"type": "string"}, "library_url": {"type": "string"}, "index": {"type": "integer"},
                "description": {"type": "string"}, "diameter": {"type": "string"},
                "product_id": {"type": "string"}, "vendor": {"type": "string"},
                "holder": {"type": "object", "properties": {"library_url": {"type": "string"}, "index": {"type": "integer"}}},
                "presets": {"type": "array", "items": {"type": "object", "properties": {
                    "spindle_speed": {"type": "number"}, "feed": {"type": "number"}}}}}},
            "description": "Tools to add/create. Each: {from_type:'drill'} (clone a sample of that type) OR {library_url,index} (copy); + optional description/diameter/product_id/vendor overrides, holder:{library_url,index}, presets:[{spindle_speed,feed}]."})
    .add_input_property("remove_indices", {"type": "array", "items": {"type": "integer"},
            "description": "Tool indices to remove."})
    .add_input_property("tool", {"type": "integer", "description": "Tool index (edit / where_used / parameters)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Tool parameters to set (edit): {name: expression}."})
    .add_input_property("tool_type", {"type": "string",
            "description": "Filter for list (substring on tool type, e.g. 'ball', 'drill')."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
