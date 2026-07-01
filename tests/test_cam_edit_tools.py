"""Unit tests for ``cam_edit_tools`` — read & manage CAM tool libraries + their tools.

The adsk.cam API is mocked; what we pin is the tool's OWN logic: the action dispatch (list / add /
remove / edit / where_used), resolving the target library by scope, and the per-action behaviour —
adding multiple tools by (library_url, index) reference, removing multiple by index (high-to-low so
indices stay valid), editing named tool parameters then persisting, and where_used (document scope
only). Plus the guards (unknown action/scope, library not found, where_used outside document, bad
tool reference, out-of-range remove index) and that every WRITE persists (document: updateTool /
shared: a persist callback).

The tool exposes seams so the test supplies a target without the real adsk plumbing:
  _resolve_target(scope, library) -> (target, error)  where target is a small object the handler drives.
"""

import json

from conftest import load_tool

ct = load_tool("cam_edit_tools")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Val:
    def __init__(self, v):
        self.value = v


class _Param:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.value = _Val(expr)


class _Params:
    def __init__(self, d):
        self._d = {k: _Param(k, v) for k, v in d.items()}
    def itemByName(self, name):
        return self._d.get(name)


class _Preset:
    def __init__(self):
        self.parameters = _Params({"tool_spindleSpeed": "0", "tool_feedCutting": "0"})


class _Presets:
    def __init__(self):
        self._p = []
    @property
    def count(self):
        return len(self._p)
    def item(self, i):
        return self._p[i]
    def add(self):
        p = _Preset(); self._p.append(p); return p


class _Tool:
    def __init__(self, desc, **params):
        params.setdefault("tool_description", desc)
        params.setdefault("tool_diameter", params.get("tool_diameter", "1.0"))
        self.parameters = _Params(params)
        self.presets = _Presets()
        self.desc = desc
        self.holder = None      # set when a holder JSON is assigned (build via json)
    def toJson(self):
        return json.dumps({"description": self.desc, "type": "x",
                           "holder": self.holder or {"description": "stock holder", "segments": []}})


class _SrcLib:
    """A source library to copy seed tools from (referenced by url+index)."""
    def __init__(self, tools):
        self._t = tools
    @property
    def count(self):
        return len(self._t)
    def item(self, i):
        return self._t[i]


# A 'target' the handler drives. Models the union of document-lib + shared-lib behaviour the tool needs:
#   .tools (list), .add(tool), .remove(index), .update_tool(tool), .persist(), .operations_by_tool(tool),
#   .is_document (where_used only valid here)
class _Target:
    def __init__(self, tools=(), is_document=False, ops_by_desc=None):
        self.tools = list(tools)
        self.is_document = is_document
        self.persisted = 0
        self.updated = []
        self._ops_by_desc = ops_by_desc or {}
    def add(self, tool):
        self.tools.append(tool)
    def remove(self, index):
        del self.tools[index]
    def update_tool(self, tool):
        self.updated.append(tool); return True
    def persist(self):
        self.persisted += 1
    def operations_by_tool(self, tool):
        return list(self._ops_by_desc.get(tool.desc, []))


_SRC_URL = "systemlibraryroot://Samples/Milling Tools (Metric)"


def _install(target=None, src=None):
    if target is None:
        target = _Target(tools=[_Tool("12mm Flat", tool_numberOfFlutes="3"),
                                _Tool("6mm Ball", tool_numberOfFlutes="2")])
    src = src if src is not None else _SrcLib([_Tool("A"), _Tool("B"), _Tool("C")])
    ct._resolve_target = lambda scope, library: (target, None)
    ct._source_tool = lambda url, idx: (src.item(idx), None) if 0 <= idx < src.count \
        else (None, "tool_index %d out of range" % idx)
    # creation seams (rich add): build via JSON -> a fresh _Tool carrying description + holder
    def _from_json(js):
        d = json.loads(js)
        t = _Tool(d.get("description", "built"))
        t.holder = d.get("holder")
        return t
    ct._tool_from_json = _from_json
    ct._sample_for_type = lambda ty: (_Tool("sample-" + ty, tool_type=ty), None) \
        if ty in ("drill", "ball end mill", "flat end mill") else (None, "no sample of type '%s'" % ty)
    ct._holder_json = lambda ref: ({"description": "CT40 Holder", "segments": [1, 2]}, None) \
        if isinstance(ref, dict) and ref.get("index") is not None else (None, "bad holder ref")
    return target


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self):
        _install()
        res = ct.handler(action="explode", scope="document")
        assert res["isError"] is True and "action" in res["message"].lower()

    def test_unknown_scope(self):
        _install()
        res = ct.handler(action="list", scope="moon")
        assert res["isError"] is True and "scope" in res["message"].lower()

    def test_target_not_found(self):
        ct._resolve_target = lambda scope, library: (None, "no library 'X'")
        res = ct.handler(action="list", scope="local", library="X")
        assert res["isError"] is True and "library" in res["message"].lower()

    def test_where_used_requires_document(self):
        _install(_Target(tools=[_Tool("T")], is_document=False))
        res = ct.handler(action="where_used", scope="local", tool=0)
        assert res["isError"] is True and "document" in res["message"].lower()


# ── list ─────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_tools(self):
        _install()
        out = _payload(ct.handler(action="list", scope="document"))
        assert out["tool_count"] == 2
        assert out["tools"][0]["description"] == "12mm Flat" and out["tools"][0]["index"] == 0

    def test_list_filters_by_tool_type(self):
        _install(_Target(tools=[_Tool("Flat", tool_type="flat end mill"),
                                _Tool("Ball", tool_type="ball end mill")]))
        out = _payload(ct.handler(action="list", scope="document", tool_type="ball"))
        assert out["tool_count"] == 1 and out["tools"][0]["type"] == "ball end mill"

    def test_list_libraries_when_no_library_given(self):
        # the absorbed cam_read_tool_library behavior: list shared scope, no 'library' -> the libraries
        _install()
        ct._shared_libraries = lambda scope: ([{"name": "Milling Tools (Metric)", "url": "u1"},
                                               {"name": "Haas Vf2.hub", "url": "u2"}], None)
        out = _payload(ct.handler(action="list", scope="hub"))
        assert out["library_count"] == 2
        assert "Haas Vf2.hub" in [l["name"] for l in out["libraries"]]


# ── add (multiple by reference) ─────────────────────────────────────────────

class TestAdd:
    def test_add_multiple(self):
        tgt = _install()
        out = _payload(ct.handler(action="add", scope="cloud", library="MyLib",
                                  add_tools=[{"library_url": _SRC_URL, "index": 0},
                                             {"library_url": _SRC_URL, "index": 2}]))
        assert len(tgt.tools) == 4 and out["added"] == 2
        assert tgt.persisted == 1          # shared scope persists once after the batch

    def test_add_validates_all_refs_before_adding(self):
        tgt = _install()
        res = ct.handler(action="add", scope="cloud", library="MyLib",
                         add_tools=[{"library_url": _SRC_URL, "index": 0},
                                    {"library_url": _SRC_URL, "index": 99}])
        assert res["isError"] is True and "99" in res["message"]
        assert len(tgt.tools) == 2 and tgt.persisted == 0   # nothing added/persisted on a bad ref

    def test_add_requires_refs(self):
        _install()
        res = ct.handler(action="add", scope="cloud", library="MyLib")
        assert res["isError"] is True


# ── rich add: create-by-type + holder + presets (the demo, via tool calls) ──

class TestAddRich:
    def test_create_from_type(self):
        tgt = _install()
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"},
                                             {"from_type": "ball end mill"}]))
        assert out["added"] == 2 and len(tgt.tools) == 4
        # the built tools carry the sample's description (from the cloned JSON)
        descs = [t.desc for t in tgt.tools[-2:]]
        assert "sample-drill" in descs and "sample-ball end mill" in descs

    def test_create_with_description_override_and_holder(self):
        tgt = _install()
        _payload(ct.handler(action="add", scope="cloud", library="L",
                            add_tools=[{"from_type": "drill", "description": "MCP Demo - drill",
                                        "holder": {"library_url": "h", "index": 0}}]))
        built = tgt.tools[-1]
        assert built.desc == "MCP Demo - drill"
        assert built.holder == {"description": "CT40 Holder", "segments": [1, 2]}

    def test_create_with_presets(self):
        tgt = _install()
        _payload(ct.handler(action="add", scope="cloud", library="L",
                            add_tools=[{"from_type": "drill",
                                        "presets": [{"spindle_speed": 10000, "feed": 500},
                                                    {"spindle_speed": 6000}]}]))
        built = tgt.tools[-1]
        assert built.presets.count == 2
        assert built.presets.item(0).parameters.itemByName("tool_spindleSpeed").expression == "10000"
        assert built.presets.item(0).parameters.itemByName("tool_feedCutting").expression == "500"

    def test_unknown_from_type_errors_before_adding(self):
        tgt = _install()
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill"}, {"from_type": "banana mill"}])
        assert res["isError"] is True and "banana mill" in res["message"]
        assert len(tgt.tools) == 2 and tgt.persisted == 0   # validate-all-before-add

    def test_entry_needs_type_or_ref(self):
        _install()
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"description": "no source"}])
        assert res["isError"] is True


# ── remove (multiple) ────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_multiple_high_to_low(self):
        # removing indices 0 and 2 must delete the RIGHT tools (remove high-to-low so indices stay valid)
        tgt = _install(_Target(tools=[_Tool("zero"), _Tool("one"), _Tool("two")]))
        out = _payload(ct.handler(action="remove", scope="local", library="L", remove_indices=[0, 2]))
        remaining = [t.desc for t in tgt.tools]
        assert remaining == ["one"] and out["removed"] == 2
        assert tgt.persisted == 1

    def test_remove_out_of_range(self):
        tgt = _install(_Target(tools=[_Tool("only")]))
        res = ct.handler(action="remove", scope="local", library="L", remove_indices=[5])
        assert res["isError"] is True and "range" in res["message"].lower()
        assert len(tgt.tools) == 1 and tgt.persisted == 0


# ── edit tool data ───────────────────────────────────────────────────────────

class TestEdit:
    def test_edit_parameters_and_persist_document(self):
        tgt = _install(_Target(tools=[_Tool("EM", tool_numberOfFlutes="3")], is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert tgt.tools[0].parameters.itemByName("tool_numberOfFlutes").expression == "4"
        assert out["edited"] == 1
        # document scope persists via update_tool (not the shared persist())
        assert tgt.updated and tgt.persisted == 0

    def test_edit_unknown_parameter_before_applying(self):
        tgt = _install(_Target(tools=[_Tool("EM", tool_numberOfFlutes="3")], is_document=True))
        res = ct.handler(action="edit", scope="document", tool=0,
                         parameters={"tool_numberOfFlutes": "4", "ghost": "9"})
        assert res["isError"] is True and "ghost" in res["message"]
        # the valid one was NOT applied (validate all first)
        assert tgt.tools[0].parameters.itemByName("tool_numberOfFlutes").expression == "3"


# ── where_used (document scope) ─────────────────────────────────────────────

class TestWhereUsed:
    def test_where_used_lists_operations(self):
        tgt = _Target(tools=[_Tool("EM")], is_document=True,
                      ops_by_desc={"EM": ["Face1", "Adaptive1"]})
        _install(tgt)
        out = _payload(ct.handler(action="where_used", scope="document", tool=0))
        assert out["operations"] == ["Face1", "Adaptive1"] and out["operation_count"] == 2


# ── create_library (folded from cam_create_tool_library) ────────────────────

class _NewLib:
    def __init__(self):
        self.tools = []
    def add(self, t):
        self.tools.append(t)
    @property
    def count(self):
        return len(self.tools)


class _CreateLibs:
    """Stand-in for ToolLibraries' create path."""
    def __init__(self):
        self.imported = []
    def urlByLocation(self, loc):
        return _URL_C({"Local": "toollibraryroot://Local", "Cloud": "cloud://",
                       "Hub": "hub://"}[loc])
    def childFolderURLs(self, url):
        return [_URL_C("hub://Team")] if url.toString() == "hub://" else []
    def importToolLibrary(self, lib, dest, name):
        if dest.toString().startswith("systemlibraryroot://"):
            raise RuntimeError("read-only")
        self.imported.append((lib, dest, name))
        return _URL_C(dest.toString().rstrip("/") + "/" + name)


class _URL_C:
    def __init__(self, s):
        self._s = s
    def toString(self):
        return self._s


def _install_create(src_count=3):
    libs = _CreateLibs()
    ct._tool_libraries = lambda: libs
    # friendly scope -> the fake's urlByLocation key
    ct._empty_library = _NewLib
    src = _SrcLib([_Tool("A"), _Tool("B"), _Tool("C")][:src_count])
    ct._source_tool = lambda url, idx: (src.item(idx), None) if 0 <= idx < src.count \
        else (None, "tool_index %d out of range" % idx)
    # map LibraryLocations attr lookups: the tool does getattr(LibraryLocations, 'LocalLibraryLocation')
    # then libs.urlByLocation(that) — our fake's urlByLocation expects friendly names, so shim it.
    import adsk.cam as _c
    _c.LibraryLocations.LocalLibraryLocation = "Local"
    _c.LibraryLocations.CloudLibraryLocation = "Cloud"
    _c.LibraryLocations.HubLibraryLocation = "Hub"
    return libs


class TestCreateLibrary:
    def test_create_empty_local(self):
        libs = _install_create()
        out = _payload(ct.handler(action="create_library", scope="local", library="MCP Test Local"))
        assert len(libs.imported) == 1
        _, dest, name = libs.imported[0]
        assert name == "MCP Test Local" and dest.toString() == "toollibraryroot://Local"
        assert out["created_library"] == "MCP Test Local" and out["tool_count"] == 0

    def test_create_with_seeds(self):
        libs = _install_create()
        out = _payload(ct.handler(action="create_library", scope="cloud", library="MCP Test Cloud",
                                  add_tools=[{"library_url": "u", "index": 0},
                                             {"library_url": "u", "index": 1}]))
        lib, _, _ = libs.imported[0]
        assert lib.count == 2 and out["tool_count"] == 2

    def test_hub_descends_to_team_folder(self):
        libs = _install_create()
        ct.handler(action="create_library", scope="hub", library="MCP Test Hub")
        _, dest, _ = libs.imported[0]
        assert dest.toString() == "hub://Team"      # not the bare hub:// root

    def test_refuses_document_scope(self):
        _install_create()
        res = ct.handler(action="create_library", scope="document", library="X")
        assert res["isError"] is True

    def test_requires_name(self):
        _install_create()
        res = ct.handler(action="create_library", scope="local", library="")
        assert res["isError"] is True and "name" in res["message"].lower()

    def test_bad_seed_before_import(self):
        libs = _install_create()
        res = ct.handler(action="create_library", scope="local", library="X",
                         add_tools=[{"library_url": "u", "index": 99}])
        assert res["isError"] is True and "99" in res["message"]
        assert len(libs.imported) == 0
