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
import re

from conftest import load_tool

ct = load_tool("cam_edit_tools")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Val:
    def __init__(self, v):
        self.value = v


class _Param:
    """expression is a property so setting it to a quoted-string literal ("'text'") updates .value to
    the unquoted text - mirroring live ModelParameter behavior for a string parameter set that way
    (tool_productId/tool_vendor). A plain numeric/unquoted expression leaves .value untouched, same as
    every other existing test in this file expects."""
    def __init__(self, name, expr):
        self.name = name
        self._expr = expr
        self.value = _Val(expr)

    @property
    def expression(self):
        return self._expr

    @expression.setter
    def expression(self, v):
        self._expr = v
        if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] == "'":
            self.value = _Val(v[1:-1])
        elif isinstance(v, str) and v.lstrip("-").isdigit():
            # a bare integer expression evaluates into the value (mirrors a ModelParameter set that
            # way - e.g. tool_number, which cam_edit_tools sets via .expression and reads via .value)
            self.value = _Val(int(v))
        elif isinstance(v, str):
            evaluated = _evaluate(v)
            if evaluated is not None:
                self.value = _Val(evaluated)


_IN_PER_MIN = re.compile(r"^([+-]?[\d.]+)\s*in/min$")


def _evaluate(expr):
    """A CAM parameter's evaluated value for the expression forms these tests use, or None when the
    expression evaluates to nothing (leaving .value where it was, which is what a preset parameter
    given an unevaluable expression does live). A feed carrying inch units converts to mm/min."""
    try:
        return float(expr.strip())
    except ValueError:
        pass
    m = _IN_PER_MIN.match(expr.strip())
    return float(m.group(1)) * 25.4 if m else None


class _Params:
    def __init__(self, d):
        self._d = {k: _Param(k, v) for k, v in d.items()}
    def itemByName(self, name):
        return self._d.get(name)
    @property
    def count(self):
        return len(self._d)
    def item(self, i):
        return list(self._d.values())[i]


# A preset's parameter set is the owning tool's cutting data, and that set varies by tool CLASS: a
# mill turns at a spindle speed, a turning tool that cuts at constant surface speed carries
# tool_surfaceSpeed and no tool_spindleSpeed at all.
_MILL_CUTTING_DATA = {"tool_spindleSpeed": "0", "tool_feedCutting": "0"}
_TURNING_CUTTING_DATA = {"tool_surfaceSpeed": "0", "tool_feedCutting": "0"}


class _Preset:
    def __init__(self, name="", params=None):
        self.name = name
        self.parameters = _Params(dict(params if params is not None else _MILL_CUTTING_DATA))


class _Presets:
    """ToolPresets: add() appends a preset carrying the owning tool's cutting-data parameters,
    remove(index) deletes by index and reports whether it deleted."""
    def __init__(self, names=(), owner=None):
        self._owner_params = dict(owner.preset_params) if owner is not None else dict(_MILL_CUTTING_DATA)
        self._p = [_Preset(n, self._owner_params) for n in names]
    @property
    def count(self):
        return len(self._p)
    def item(self, i):
        return self._p[i]
    def add(self):
        p = _Preset("", self._owner_params); self._p.append(p); return p
    def remove(self, index):
        del self._p[index]
        return True


def _preset_names(tool):
    return [tool.presets.item(i).name for i in range(tool.presets.count)]


class _Tool:
    def __init__(self, desc, preset_params=None, **params):
        params.setdefault("tool_description", desc)
        params.setdefault("tool_diameter", params.get("tool_diameter", "1.0"))
        params.setdefault("tool_productId", "")
        params.setdefault("tool_vendor", "")
        params.setdefault("tool_number", params.get("tool_number", "0"))
        self.parameters = _Params(params)
        # the cutting data this tool's presets carry (mill unless the test says otherwise)
        self.preset_params = dict(preset_params if preset_params is not None else _MILL_CUTTING_DATA)
        self.presets = _Presets(owner=self)
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


def _refetched(tool):
    """One tool as a library re-read from its url hands it back: a DIFFERENT object carrying the same
    stored values. Every persist read-back must go through one of these - a read-back satisfied by the
    object the write just touched proves nothing. (Preset PARAMETER values are not copied; the
    re-read only ever reports preset NAMES.)"""
    clone = _Tool(tool.desc)
    clone.parameters = _Params({tool.parameters.item(i).name: tool.parameters.item(i).expression
                                for i in range(tool.parameters.count)})
    clone.preset_params = dict(tool.preset_params)
    clone.presets = _Presets(_preset_names(tool), owner=clone)
    clone.holder = tool.holder
    return clone


# A 'target' the handler drives. Models the union of document-lib + shared-lib behaviour the tool needs:
#   .tools (list), .add(tool), .remove(index), .update_tool(tool), .persist(), .operations_by_tool(tool),
#   .is_document (where_used only valid here), and the refetch read-backs off _fresh()
class _Target:
    def __init__(self, tools=(), is_document=False, ops_by_desc=None, persisted_count_value="mirror"):
        self.tools = list(tools)
        self.is_document = is_document
        self.persisted = 0
        self.updated = []
        self._ops_by_desc = ops_by_desc or {}
        # 'mirror' = the url re-read agrees with the in-memory tools (a landing persist);
        # a NUMBER simulates a persist whose url re-read disagrees (the platform lie).
        self._persisted_count_value = persisted_count_value

    def _fresh(self):
        """The library re-read from its url - FRESH clones of what it stored, never the held objects.
        A test sets `tgt._fresh = lambda: None` to simulate a library that cannot be re-read (every
        read-back then reports None and the payload must fall back to the in-memory basis)."""
        return [_refetched(t) for t in self.tools]

    def persisted_count(self):
        if self._persisted_count_value != "mirror":
            return self._persisted_count_value
        fresh = self._fresh()
        return len(fresh) if fresh is not None else None
    def reread_param(self, index, name):
        # read off the FRESH clone, so a landing edit reads back its own 'after'; a test overrides
        # this to simulate a library that stored something else.
        fresh = self._fresh()
        if fresh is None:
            return None
        p = fresh[index].parameters.itemByName(name)
        return p.expression if p is not None else None
    def reread_preset_names(self, index):
        # same fresh re-read for presets; a test overrides it to simulate a persist that the
        # library did not store.
        fresh = self._fresh()
        return _preset_names(fresh[index]) if fresh is not None else None
    def stored_tool_numbers(self):
        # the numbers the STORED library holds; a test overrides it to simulate a persist-side
        # renumber, which no in-memory read could catch.
        fresh = self._fresh()
        return None if fresh is None else [ct._read_tool_number(t) for t in fresh]
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


def _install(monkeypatch, target=None, src=None):
    if target is None:
        target = _Target(tools=[_Tool("12mm Flat", tool_numberOfFlutes="3"),
                                _Tool("6mm Ball", tool_numberOfFlutes="2")])
    src = src if src is not None else _SrcLib([_Tool("A"), _Tool("B"), _Tool("C")])
    monkeypatch.setattr(ct, "_resolve_target", lambda scope, library: (target, None))
    monkeypatch.setattr(ct, "_source_tool", lambda url, idx: (src.item(idx), None) if 0 <= idx < src.count
                        else (None, "tool_index %d out of range" % idx))
    # creation seams (rich add): build via JSON -> a fresh _Tool carrying description + holder
    def _from_json(js):
        d = json.loads(js)
        t = _Tool(d.get("description", "built"))
        t.holder = d.get("holder")
        return t
    monkeypatch.setattr(ct, "_tool_from_json", _from_json)
    monkeypatch.setattr(ct, "_sample_for_type", lambda ty: (_Tool("sample-" + ty, tool_type=ty), None)
                        if ty in ("drill", "ball end mill", "flat end mill")
                        else (None, "no sample of type '%s'" % ty))
    monkeypatch.setattr(ct, "_holder_json", lambda ref: ({"description": "CT40 Holder", "segments": [1, 2]}, None)
                        if isinstance(ref, dict) and ref.get("index") is not None
                        else (None, "bad holder ref"))
    return target


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self, monkeypatch):
        _install(monkeypatch)
        res = ct.handler(action="explode", scope="document")
        assert res["isError"] is True and "action" in res["message"].lower()

    def test_unknown_scope(self, monkeypatch):
        _install(monkeypatch)
        res = ct.handler(action="list", scope="moon")
        assert res["isError"] is True and "scope" in res["message"].lower()

    def test_target_not_found(self, monkeypatch):
        monkeypatch.setattr(ct, "_resolve_target", lambda scope, library: (None, "no library 'X'"))
        res = ct.handler(action="list", scope="local", library="X")
        assert res["isError"] is True and "library" in res["message"].lower()

    def test_where_used_requires_document(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("T")], is_document=False))
        res = ct.handler(action="where_used", scope="local", tool=0)
        assert res["isError"] is True and "document" in res["message"].lower()


# ── list ─────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_tools(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(ct.handler(action="list", scope="document"))
        assert out["tool_count"] == 2
        assert out["tools"][0]["description"] == "12mm Flat" and out["tools"][0]["index"] == 0

    def test_list_shows_tool_product_id_vendor_and_number(self, monkeypatch):
        # the CUTTING tool's own product identity + its tool number ride the list row so a read can
        # confirm what the add path set (no list read could confirm them before).
        _install(monkeypatch, _Target(tools=[_Tool("Drill", tool_productId="HAM-123",
                                                    tool_vendor="Hoffmann", tool_number="7")]))
        out = _payload(ct.handler(action="list", scope="document"))
        row = out["tools"][0]
        assert row["tool_product_id"] == "HAM-123" and row["tool_vendor"] == "Hoffmann"
        assert row["number"] == 7

    def test_list_omits_empty_product_identity(self, monkeypatch):
        # empty product_id/vendor are dropped (present-only), not shown as blank fields.
        _install(monkeypatch, _Target(tools=[_Tool("Plain")]))
        out = _payload(ct.handler(action="list", scope="document"))
        row = out["tools"][0]
        assert "tool_product_id" not in row and "tool_vendor" not in row

    def test_list_filters_by_tool_type(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("Flat", tool_type="flat end mill"),
                                _Tool("Ball", tool_type="ball end mill")]))
        out = _payload(ct.handler(action="list", scope="document", tool_type="ball"))
        assert out["tool_count"] == 1 and out["tools"][0]["type"] == "ball end mill"

    def test_list_libraries_when_no_library_given(self, monkeypatch):
        # list, shared scope, no 'library' -> the libraries at that scope
        _install(monkeypatch)
        monkeypatch.setattr(ct, "_shared_libraries",
                            lambda scope: ([{"name": "Milling Tools (Metric)", "url": "u1"},
                                           {"name": "Team Mill.hub", "url": "u2"}], None))
        out = _payload(ct.handler(action="list", scope="hub"))
        assert out["library_count"] == 2
        assert "Team Mill.hub" in [l["name"] for l in out["libraries"]]


# ── list_types (the from_type vocabulary, no document/scope/library needed) ─────────────────────────

class TestListTypes:
    def test_lists_the_type_map_no_target_resolution(self, monkeypatch):
        # No _resolve_target seam installed at all - list_types must not need one.
        monkeypatch.setattr(ct, "_build_type_map", lambda: {"drill": ("u", 0), "ball end mill": ("u", 1)})
        out = _payload(ct.handler(action="list_types"))
        assert out["type_count"] == 2
        assert out["types"] == ["ball end mill", "drill"]      # sorted

    def test_empty_type_map_errors(self, monkeypatch):
        monkeypatch.setattr(ct, "_build_type_map", lambda: {})
        res = ct.handler(action="list_types")
        assert res["isError"] is True


# ── add (multiple by reference) ─────────────────────────────────────────────

class TestAdd:
    def test_add_multiple(self, monkeypatch):
        tgt = _install(monkeypatch)
        out = _payload(ct.handler(action="add", scope="cloud", library="MyLib",
                                  add_tools=[{"library_url": _SRC_URL, "index": 0},
                                             {"library_url": _SRC_URL, "index": 2}]))
        assert len(tgt.tools) == 4 and out["added"] == 2
        assert tgt.persisted == 1          # shared scope persists once after the batch

    def test_persist_whose_url_reread_disagrees_bites(self, monkeypatch):
        # updateToolLibrary reports success but the library re-read fresh from its url holds the
        # wrong tool count (updateToolLibrary returning True is NOT proof) -> error, not ok
        tgt = _Target(tools=[_Tool("12mm Flat")], persisted_count_value=1)
        _install(monkeypatch, target=tgt)
        res = ct.handler(action="add", scope="cloud", library="MyLib",
                         add_tools=[{"library_url": _SRC_URL, "index": 0}])
        assert res["isError"] is True
        assert "did not land" in res["message"]

    def test_add_validates_all_refs_before_adding(self, monkeypatch):
        tgt = _install(monkeypatch)
        res = ct.handler(action="add", scope="cloud", library="MyLib",
                         add_tools=[{"library_url": _SRC_URL, "index": 0},
                                    {"library_url": _SRC_URL, "index": 99}])
        assert res["isError"] is True and "99" in res["message"]
        assert len(tgt.tools) == 2 and tgt.persisted == 0   # nothing added/persisted on a bad ref

    def test_add_requires_refs(self, monkeypatch):
        _install(monkeypatch)
        res = ct.handler(action="add", scope="cloud", library="MyLib")
        assert res["isError"] is True
        assert "Provide 'add_tools'" in res["message"]


# ── rich add: create-by-type + holder + presets (the demo, via tool calls) ──

class TestAddRich:
    def test_create_from_type(self, monkeypatch):
        tgt = _install(monkeypatch)
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"},
                                             {"from_type": "ball end mill"}]))
        assert out["added"] == 2 and len(tgt.tools) == 4
        # the built tools carry the sample's description (from the cloned JSON)
        descs = [t.desc for t in tgt.tools[-2:]]
        assert "sample-drill" in descs and "sample-ball end mill" in descs

    def test_create_with_description_override_and_holder(self, monkeypatch):
        tgt = _install(monkeypatch)
        _payload(ct.handler(action="add", scope="cloud", library="L",
                            add_tools=[{"from_type": "drill", "description": "MCP Demo - drill",
                                        "holder": {"library_url": "h", "index": 0}}]))
        built = tgt.tools[-1]
        assert built.desc == "MCP Demo - drill"
        assert built.holder == {"description": "CT40 Holder", "segments": [1, 2]}

    def test_create_with_presets(self, monkeypatch):
        tgt = _install(monkeypatch)
        _payload(ct.handler(action="add", scope="cloud", library="L",
                            add_tools=[{"from_type": "drill",
                                        "presets": [{"spindle_speed": 10000, "feed": 500},
                                                    {"spindle_speed": 6000}]}]))
        built = tgt.tools[-1]
        assert built.presets.count == 2
        assert built.presets.item(0).parameters.itemByName("tool_spindleSpeed").expression == "10000"
        assert built.presets.item(0).parameters.itemByName("tool_feedCutting").expression == "500"

    def test_preset_add_failure_errors_not_silent_skip(self, monkeypatch):
        # A preset that cannot be created must abort the add - skipping it would report "added"
        # while silently dropping the requested preset.
        tgt = _install(monkeypatch)

        class _NoPresets(_Presets):
            def add(self):
                raise RuntimeError("presets locked")

        def _from_json(js):
            t = _Tool(json.loads(js).get("description", "built"))
            t.presets = _NoPresets()
            return t

        monkeypatch.setattr(ct, "_tool_from_json", _from_json)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill", "presets": [{"spindle_speed": 10000}]}])
        assert res["isError"] is True and "preset" in res["message"].lower()
        assert len(tgt.tools) == 2          # nothing was added

    def test_missing_diameter_param_errors_not_silent_drop(self, monkeypatch):
        # A tool without a tool_diameter parameter cannot take the requested override - that is
        # an error, not a tool silently added at its default diameter.
        tgt = _install(monkeypatch)

        def _from_json(js):
            t = _Tool(json.loads(js).get("description", "built"))
            t.parameters = _Params({})      # no tool_diameter parameter
            return t

        monkeypatch.setattr(ct, "_tool_from_json", _from_json)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill", "diameter": 8}])
        assert res["isError"] is True and "tool_diameter" in res["message"]
        assert len(tgt.tools) == 2          # nothing was added

    def test_diameter_setter_raise_propagates(self, monkeypatch):
        # A diameter-override setter failure must propagate out of the handler - swallowing it
        # would add the tool while silently dropping the requested override.
        import pytest

        class _LockedParam(_Param):
            @property
            def expression(self):
                return self._expr
            @expression.setter
            def expression(self, v):
                raise AttributeError("expression is locked")
            def __init__(self, name, expr):
                self.name = name
                self._expr = expr
                self.value = _Val(expr)

        class _LockedParams(_Params):
            def itemByName(self, name):
                p = self._d.get(name)
                if p is not None and name == "tool_diameter":
                    return _LockedParam(p.name, p.expression)
                return p

        class _LockedTool(_Tool):
            def __init__(self, desc, **params):
                super().__init__(desc, **params)
                self.parameters = _LockedParams({"tool_diameter": "1.0", "tool_description": desc})

        _install(monkeypatch)
        monkeypatch.setattr(ct, "_tool_from_json", lambda js: _LockedTool("sample-drill"))
        with pytest.raises(AttributeError, match="expression is locked"):
            ct.handler(action="add", scope="cloud", library="L",
                       add_tools=[{"from_type": "drill", "diameter": "6 mm"}])

    def test_unknown_from_type_errors_before_adding(self, monkeypatch):
        tgt = _install(monkeypatch)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill"}, {"from_type": "banana mill"}])
        assert res["isError"] is True and "banana mill" in res["message"]
        assert len(tgt.tools) == 2 and tgt.persisted == 0   # validate-all-before-add

    def test_entry_needs_type_or_ref(self, monkeypatch):
        _install(monkeypatch)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"description": "no source"}])
        assert res["isError"] is True
        assert "needs 'from_type'" in res["message"]


# ── add: product_id / vendor (tool_productId/tool_vendor, applied as expressions AFTER creation - ──
# ── createFromJson's JSON schema silently drops these keys, verified live) ──────────────────────────

class TestAddProductVendor:
    def test_product_id_and_vendor_applied_and_read_back(self, monkeypatch):
        tgt = _install(monkeypatch)
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill", "product_id": "HAM-123",
                                              "vendor": "Hoffmann Group"}]))
        built = tgt.tools[-1]
        assert built.parameters.itemByName("tool_productId").value.value == "HAM-123"
        assert built.parameters.itemByName("tool_vendor").value.value == "Hoffmann Group"
        assert out["added"] == 1

    def test_missing_product_id_param_errors_not_silent_drop(self, monkeypatch):
        # A tool without a tool_productId parameter cannot take the requested override.
        tgt = _install(monkeypatch)

        def _from_json(js):
            t = _Tool(json.loads(js).get("description", "built"))
            t.parameters = _Params({"tool_description": t.desc, "tool_diameter": "1.0"})
            return t

        monkeypatch.setattr(ct, "_tool_from_json", _from_json)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill", "product_id": "HAM-123"}])
        assert res["isError"] is True and "tool_productId" in res["message"]
        assert len(tgt.tools) == 2          # nothing was added

    def test_product_id_readback_mismatch_errors(self, monkeypatch):
        # The expression sets without raising, but the parameter's evaluated .value never moves - the
        # THIRD failure mode (looks identical to success from the setter alone) that the original
        # add_tools product_id/vendor defect actually was; the read-back must catch it.
        tgt = _install(monkeypatch)

        class _StaleParam(_Param):
            @property
            def expression(self):
                return self._expr
            @expression.setter
            def expression(self, v):
                self._expr = v          # accepted - but .value deliberately stays put

        def _from_json(js):
            t = _Tool(json.loads(js).get("description", "built"))
            t.parameters._d["tool_productId"] = _StaleParam("tool_productId", "")
            return t

        monkeypatch.setattr(ct, "_tool_from_json", _from_json)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill", "product_id": "HAM-123"}])
        assert res["isError"] is True and "did not land" in res["message"]
        assert len(tgt.tools) == 2          # nothing was added


# ── add: auto-assign a FREE tool_number so multiple adds don't collide (cam_post refuses dupes) ──

class TestAddToolNumbers:
    def test_assigns_next_free_numbers_from_zero(self, monkeypatch):
        # the two existing tools both sit at tool_number 0 (unassigned); the two new tools must get
        # the next FREE numbers (1, 2), never keep the cloned sample's number.
        tgt = _install(monkeypatch)
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"}, {"from_type": "ball end mill"}]))
        assert out["assigned_tool_numbers"] == [1, 2]
        added = tgt.tools[-2:]
        assert [ct._read_tool_number(t) for t in added] == [1, 2]

    def test_skips_numbers_already_in_use(self, monkeypatch):
        # existing tools already hold 1 and 3 - a new tool gets 2 (the first free), not a collision.
        tgt = _install(monkeypatch, _Target(tools=[_Tool("A", tool_number="1"),
                                                   _Tool("B", tool_number="3")]))
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"}]))
        assert out["assigned_tool_numbers"] == [2]
        assert ct._read_tool_number(tgt.tools[-1]) == 2

    def test_the_library_refetch_is_a_different_object(self):
        # The fake's own contract: a read-back reads a FRESH clone, so no production read-back can
        # pass by handing back the very object it just wrote.
        tgt = _Target(tools=[_Tool("EM", tool_number="5")])
        fresh = tgt._fresh()
        assert fresh[0] is not tgt.tools[0]
        assert ct._read_tool_number(fresh[0]) == 5

    def test_numbers_are_confirmed_against_the_fresh_library(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"}]))
        assert out["verified_in_memory_only"] is False
        assert "persisted" in out["note"] and "library url" in out["note"]

    def test_document_scope_add_never_claims_the_stored_library(self, monkeypatch):
        # The document library has NO url to re-read, and doc_save is what stores the document. So a
        # document add proves the numbers are present, never that they were stored - the flag stays
        # weak and the note says so.
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM")], is_document=True))
        out = _payload(ct.handler(action="add", scope="document",
                                  add_tools=[{"from_type": "drill"}]))
        assert out["assigned_tool_numbers"] == [1] and len(tgt.tools) == 2
        assert out["verified_in_memory_only"] is True
        # the add skips the document read-back, so the note must claim NO read-back - only the
        # in-memory tools - and point at what does the storing
        assert "in-memory" in out["note"] and "doc_save" in out["note"]
        assert "persist" not in out["note"].lower() and "url" not in out["note"]

    def test_document_scope_add_does_not_reach_for_a_stored_library(self, monkeypatch):
        # ... and it does not even ask: the stored-number gate is shared-target-only, so a document
        # add makes no refetch-for-storage call it would then have to explain.
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM")], is_document=True))

        def _boom():
            raise AssertionError("a document target must not read a stored library")
        tgt.stored_tool_numbers = _boom
        out = _payload(ct.handler(action="add", scope="document",
                                  add_tools=[{"from_type": "drill"}]))
        assert out["added"] == 1

    def test_persist_side_renumber_bites(self, monkeypatch):
        # The in-memory tools hold the assigned numbers, and the tool COUNT is right - only the
        # library re-read from its url shows the stored number is a different one.
        tgt = _install(monkeypatch)
        tgt.stored_tool_numbers = lambda: [0, 0, 99]     # the new tool was stored as 99, not 1
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill"}])
        assert res["isError"] is True
        assert "[1]" in res["message"] and "99" in res["message"]
        assert "did not reach the stored library" in res["message"]

    def test_unreadable_refetch_names_the_weaker_basis(self, monkeypatch):
        # The library cannot be re-read: the in-memory verdict stands, but the payload must SAY the
        # numbers were only confirmed there - never claim the stored library agreed.
        tgt = _install(monkeypatch)
        tgt._fresh = lambda: None
        out = _payload(ct.handler(action="add", scope="cloud", library="L",
                                  add_tools=[{"from_type": "drill"}]))
        assert out["assigned_tool_numbers"] == [1]
        assert out["verified_in_memory_only"] is True
        assert "in-memory" in out["note"]

    def test_missing_tool_number_param_errors_not_silent(self, monkeypatch):
        # a tool with no tool_number parameter can't take a free number - error, add nothing.
        tgt = _install(monkeypatch)

        def _from_json(js):
            t = _Tool(json.loads(js).get("description", "built"))
            del t.parameters._d["tool_number"]
            return t

        monkeypatch.setattr(ct, "_tool_from_json", _from_json)
        res = ct.handler(action="add", scope="cloud", library="L", add_tools=[{"from_type": "drill"}])
        assert res["isError"] is True and "tool_number" in res["message"]
        assert len(tgt.tools) == 2          # nothing was added


# ── remove (multiple) ────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_multiple_high_to_low(self, monkeypatch):
        # removing indices 0 and 2 must delete the RIGHT tools (remove high-to-low so indices stay valid)
        tgt = _install(monkeypatch, _Target(tools=[_Tool("zero"), _Tool("one"), _Tool("two")]))
        out = _payload(ct.handler(action="remove", scope="local", library="L", remove_indices=[0, 2]))
        remaining = [t.desc for t in tgt.tools]
        assert remaining == ["one"] and out["removed"] == 2
        assert tgt.persisted == 1

    def test_remove_out_of_range(self, monkeypatch):
        tgt = _install(monkeypatch, _Target(tools=[_Tool("only")]))
        res = ct.handler(action="remove", scope="local", library="L", remove_indices=[5])
        assert res["isError"] is True and "range" in res["message"].lower()
        assert len(tgt.tools) == 1 and tgt.persisted == 0


# ── edit tool data ───────────────────────────────────────────────────────────

class TestEdit:
    def test_edit_parameters_and_persist_document(self, monkeypatch):
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")], is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert tgt.tools[0].parameters.itemByName("tool_numberOfFlutes").expression == "4"
        assert out["edited"] == 1
        # document scope persists via update_tool (not the shared persist())
        assert tgt.updated and tgt.persisted == 0

    def test_edit_unknown_parameter_before_applying(self, monkeypatch):
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")], is_document=True))
        res = ct.handler(action="edit", scope="document", tool=0,
                         parameters={"tool_numberOfFlutes": "4", "ghost": "9"})
        assert res["isError"] is True and "ghost" in res["message"]
        # the valid one was NOT applied (validate all first)
        assert tgt.tools[0].parameters.itemByName("tool_numberOfFlutes").expression == "3"

    def test_warns_when_overwriting_a_formula_derived_parameter(self, monkeypatch):
        # tool_shoulderLength's expression is the literal string "tool_fluteLength" - it tracks that
        # OTHER parameter rather than holding an independent literal (verified live). The edit still
        # goes through (the warning is the teaching, not a refusal).
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_shoulderLength="tool_fluteLength",
                                                          tool_fluteLength="16")], is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_shoulderLength": "21 mm"}))
        assert out["warnings"] and "tool_fluteLength" in out["warnings"][0]
        assert tgt.tools[0].parameters.itemByName("tool_shoulderLength").expression == "21 mm"

    def test_no_warning_when_overwriting_a_literal_parameter(self, monkeypatch):
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")], is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert "warnings" not in out

    def test_setter_raise_mid_edit_names_property_and_applied(self, monkeypatch):
        # the SECOND property's setter raises -> isError naming the FAILING property AND what already
        # landed (partial success surfaced explicitly), and the partial edit is never persisted.
        class _Boom(_Param):
            @property
            def expression(self):
                return self._expr
            @expression.setter
            def expression(self, v):
                raise RuntimeError("locked by Fusion")

        tool = _Tool("EM", tool_numberOfFlutes="3")
        tool.parameters._d["tool_coolant"] = _Boom("tool_coolant", "flood")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="edit", scope="document", tool=0,
                         parameters={"tool_numberOfFlutes": "4", "tool_coolant": "mist"})
        assert res["isError"] is True
        assert "tool_coolant" in res["message"] and "locked by Fusion" in res["message"]
        assert "tool_numberOfFlutes" in res["message"]        # what DID land is named, not hidden
        assert tgt.updated == [] and tgt.persisted == 0       # the partial edit was not persisted

    def test_setter_raise_with_nothing_applied_says_none(self, monkeypatch):
        class _Boom(_Param):
            @property
            def expression(self):
                return self._expr
            @expression.setter
            def expression(self, v):
                raise RuntimeError("locked")

        tool = _Tool("EM")
        tool.parameters._d["tool_coolant"] = _Boom("tool_coolant", "flood")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="edit", scope="document", tool=0,
                         parameters={"tool_coolant": "mist"})
        assert res["isError"] is True and "tool_coolant" in res["message"]
        assert "none" in res["message"]                       # the Applied: list is honest about zero
        assert tgt.updated == [] and tgt.persisted == 0

    def test_edit_reports_before_and_read_back_after(self, monkeypatch):
        # 'after' is read BACK off the parameter (the platform can normalize what it stores) -
        # never an echo of the requested expression.
        class _Norm(_Param):
            @property
            def expression(self):
                return self._expr
            @expression.setter
            def expression(self, v):
                self._expr = "4.000 mm"      # platform normalizes the stored expression

        tool = _Tool("EM")
        tool.parameters._d["tool_diameter"] = _Norm("tool_diameter", "1.0")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_diameter": "4 mm"}))
        assert out["changed"][0] == {"name": "tool_diameter", "before": "1.0", "after": "4.000 mm"}

    def test_persist_readback_mismatch_bites(self, monkeypatch):
        # updateTool/updateToolLibrary returning is not proof the edit stored - the tool re-read from
        # the library holds a DIFFERENT value than what we set -> error, never a false 'edited'.
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")],
                                            is_document=True))
        tgt.reread_param = lambda index, name: "3"      # library reports a value the edit did not store
        res = ct.handler(action="edit", scope="document", tool=0,
                         parameters={"tool_numberOfFlutes": "4"})
        assert res["isError"] is True and "did not persist" in res["message"]

    def test_document_scope_edit_never_claims_storage_though_the_refetch_reads(self, monkeypatch):
        # The document refetch READS fine and the edit is confirmed present - but the document
        # library has no url, so nothing here proves storage. The flag must stay weak on the
        # readable path too, and the note must name the document rung.
        _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")],
                                      is_document=True))
        out = _payload(ct.handler(action="edit", scope="document", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert out["edited"] == 1                       # the effect still reports
        assert out["verified_in_memory_only"] is True
        assert "present, not that it was stored" in out["note"] and "doc_save" in out["note"]
        assert "persist" not in out["note"].lower()

    def test_edit_confirmed_against_the_fresh_library_says_so(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")]))
        out = _payload(ct.handler(action="edit", scope="cloud", library="L", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert out["verified_in_memory_only"] is False
        assert "persisted" in out["note"] and "library url" in out["note"]

    def test_edit_with_an_unreadable_refetch_names_the_weaker_basis(self, monkeypatch):
        # A library that cannot be re-read proves nothing about storage - the edit still stands on
        # the in-memory tool, and the payload says exactly that instead of claiming persistence.
        tgt = _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3")]))
        tgt._fresh = lambda: None
        out = _payload(ct.handler(action="edit", scope="cloud", library="L", tool=0,
                                  parameters={"tool_numberOfFlutes": "4"}))
        assert out["edited"] == 1
        assert out["verified_in_memory_only"] is True
        assert "persisted" not in out["note"] and "in-memory" in out["note"]


# ── where_used (document scope) ─────────────────────────────────────────────

class TestWhereUsed:
    def test_where_used_lists_operations(self, monkeypatch):
        tgt = _Target(tools=[_Tool("EM")], is_document=True,
                      ops_by_desc={"EM": ["Face1", "Adaptive1"]})
        _install(monkeypatch, tgt)
        out = _payload(ct.handler(action="where_used", scope="document", tool=0))
        assert out["operations"] == ["Face1", "Adaptive1"] and out["operation_count"] == 2


# ── parameters (the FULL per-tool parameter read the list summary points to) ──
# NEEDS-LIVE-VERIFY: against a real document with a CAM product and >=1 tool in the document library,
#   cam_edit_tools(action='parameters', scope='document', tool=0)
# expected shape: {"tool":0, "description":<str>, "parameter_count":>0, "parameters":[
#   {"name":"tool_diameter","expression":<str>,"value":<number>}, ...,
#   {"name":"tool_shoulderLength","expression":"tool_fluteLength","value":<number>,
#    "formula_source":"tool_fluteLength"}, ...]}
# Confirms live: adsk.cam.Tool.parameters exposes .count/.item(i), each parameter exposes
# .name/.expression/.value.value, and a formula-derived parameter (shoulderLength tracking
# fluteLength) reads its source name back so _formula_source flags it.

class TestParameters:
    def test_lists_every_parameter_with_expression_and_value(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="3",
                                                    tool_diameter="6 mm")]))
        out = _payload(ct.handler(action="parameters", scope="cloud", library="L", tool=0))
        assert out["parameter_count"] == len(out["parameters"])
        rows = {r["name"]: r for r in out["parameters"]}
        assert rows["tool_diameter"]["expression"] == "6 mm"
        assert rows["tool_numberOfFlutes"]["expression"] == "3"
        # every row carries name/expression/value
        assert all({"name", "expression", "value"} <= set(r) for r in out["parameters"])

    def test_flags_formula_derived_parameter(self, monkeypatch):
        # tool_shoulderLength's expression is literally another parameter's NAME - it tracks that
        # parameter rather than holding a literal, so the read flags it; a literal carries no flag.
        _install(monkeypatch, _Target(tools=[_Tool("EM", tool_shoulderLength="tool_fluteLength",
                                                    tool_fluteLength="16")]))
        out = _payload(ct.handler(action="parameters", scope="cloud", library="L", tool=0))
        rows = {r["name"]: r for r in out["parameters"]}
        assert rows["tool_shoulderLength"]["formula_source"] == "tool_fluteLength"
        assert "formula_source" not in rows["tool_fluteLength"]

    def test_value_is_read_back_off_the_parameter(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("EM", tool_numberOfFlutes="4")]))
        out = _payload(ct.handler(action="parameters", scope="cloud", library="L", tool=0))
        row = next(r for r in out["parameters"] if r["name"] == "tool_numberOfFlutes")
        assert row["value"] == "4"

    def test_bad_tool_index_errors(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("only")]))
        res = ct.handler(action="parameters", scope="cloud", library="L", tool=7)
        assert res["isError"] is True and "0..0" in res["message"]

    def test_works_on_document_scope(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_Tool("EM")], is_document=True))
        out = _payload(ct.handler(action="parameters", scope="document", tool=0))
        assert out["tool"] == 0 and out["parameter_count"] >= 1


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
    def __init__(self, loads_back=True):
        self.imported = []
        self._loads_back = loads_back     # False = the created url re-reads to nothing (the lie)
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
    def toolLibraryAtURL(self, url):
        return self.imported[-1][0] if (self._loads_back and self.imported) else None


class _URL_C:
    def __init__(self, s):
        self._s = s
    def toString(self):
        return self._s


def _install_create(monkeypatch, src_count=3):
    libs = _CreateLibs()
    monkeypatch.setattr(ct, "_tool_libraries", lambda: libs)
    # friendly scope -> the fake's urlByLocation key
    monkeypatch.setattr(ct, "_empty_library", _NewLib)
    src = _SrcLib([_Tool("A"), _Tool("B"), _Tool("C")][:src_count])
    monkeypatch.setattr(ct, "_source_tool", lambda url, idx: (src.item(idx), None) if 0 <= idx < src.count
                        else (None, "tool_index %d out of range" % idx))
    # map LibraryLocations attr lookups: the tool does getattr(LibraryLocations, 'LocalLibraryLocation')
    # then libs.urlByLocation(that) — our fake's urlByLocation expects friendly names, so shim it.
    import adsk.cam as _c
    monkeypatch.setattr(_c.LibraryLocations, "LocalLibraryLocation", "Local")
    monkeypatch.setattr(_c.LibraryLocations, "CloudLibraryLocation", "Cloud")
    monkeypatch.setattr(_c.LibraryLocations, "HubLibraryLocation", "Hub")
    return libs


class TestCreateLibrary:
    def test_create_empty_local(self, monkeypatch):
        libs = _install_create(monkeypatch)
        out = _payload(ct.handler(action="create_library", scope="local", library="MCP Test Local"))
        assert len(libs.imported) == 1
        _, dest, name = libs.imported[0]
        assert name == "MCP Test Local" and dest.toString() == "toollibraryroot://Local"
        assert out["created_library"] == "MCP Test Local" and out["tool_count"] == 0

    def test_create_with_seeds(self, monkeypatch):
        libs = _install_create(monkeypatch)
        out = _payload(ct.handler(action="create_library", scope="cloud", library="MCP Test Cloud",
                                  add_tools=[{"library_url": "u", "index": 0},
                                             {"library_url": "u", "index": 1}]))
        lib, _, _ = libs.imported[0]
        assert lib.count == 2 and out["tool_count"] == 2

    def test_created_library_that_does_not_load_back_bites(self, monkeypatch):
        # importToolLibrary returned a URL but nothing loads back from it -> error, not created
        libs = _install_create(monkeypatch)
        libs._loads_back = False
        res = ct.handler(action="create_library", scope="local", library="Ghost Lib")
        assert res["isError"] is True
        assert "did not land" in res["message"]

    def test_hub_descends_to_team_folder(self, monkeypatch):
        libs = _install_create(monkeypatch)
        ct.handler(action="create_library", scope="hub", library="MCP Test Hub")
        _, dest, _ = libs.imported[0]
        assert dest.toString() == "hub://Team"      # not the bare hub:// root

    def test_refuses_document_scope(self, monkeypatch):
        _install_create(monkeypatch)
        res = ct.handler(action="create_library", scope="document", library="X")
        assert res["isError"] is True
        assert "Cannot create a library in the document scope" in res["message"]

    def test_requires_name(self, monkeypatch):
        _install_create(monkeypatch)
        res = ct.handler(action="create_library", scope="local", library="")
        assert res["isError"] is True and "name" in res["message"].lower()

    def test_bad_seed_before_import(self, monkeypatch):
        libs = _install_create(monkeypatch)
        res = ct.handler(action="create_library", scope="local", library="X",
                         add_tools=[{"library_url": "u", "index": 99}])
        assert res["isError"] is True and "99" in res["message"]
        assert len(libs.imported) == 0


# ── _preset_param_of: a preset value maps per tool CLASS (a drill preset has no 'tool_feedCutting', ─
# ── a constant-surface-speed turning preset has no 'tool_spindleSpeed'); the resolver falls through ─
# ── the candidates, and names what exists when none match instead of asserting one class's name. ────

class _PParams:
    def __init__(self, names):
        self._names = list(names)
    def itemByName(self, name):
        return _Param(name, "0") if name in self._names else None
    @property
    def count(self):
        return len(self._names)
    def item(self, i):
        return _Param(self._names[i], "0")


def _preset_with(names):
    """A ToolPreset whose parameters collection carries exactly `names` (no bespoke fake class)."""
    from types import SimpleNamespace
    return SimpleNamespace(parameters=_PParams(names))


def _feed_param(preset):
    return ct._preset_param_of(preset, ct._FEED_PARAM_CANDIDATES, "feed")


def _speed_param(preset):
    return ct._preset_param_of(preset, ct._SPEED_PARAM_CANDIDATES, "speed")


class TestPresetParamOf:
    def test_mill_uses_tool_feed_cutting(self):
        p, avail = _feed_param(_preset_with(["tool_spindleSpeed", "tool_feedCutting"]))
        assert p is not None and p.name == "tool_feedCutting" and avail is None

    def test_drill_falls_back_to_plunge_feed(self):
        # a drill preset carries NO tool_feedCutting - the {feed} value goes to its plunge feed
        p, avail = _feed_param(_preset_with(["tool_spindleSpeed", "tool_feedPlunge"]))
        assert p is not None and p.name == "tool_feedPlunge"

    def test_no_known_feed_names_what_exists(self):
        # nothing from the candidate list -> refuse, naming the feed-ish params actually present
        p, avail = _feed_param(_preset_with(["tool_spindleSpeed", "tool_feedGizmo"]))
        assert p is None and avail == ["tool_feedGizmo"]

    def test_no_feed_params_at_all(self):
        p, avail = _feed_param(_preset_with(["tool_spindleSpeed"]))
        assert p is None and avail == []

    def test_mill_uses_tool_spindle_speed(self):
        p, avail = _speed_param(_preset_with(["tool_spindleSpeed", "tool_feedCutting"]))
        assert p is not None and p.name == "tool_spindleSpeed" and avail is None

    def test_surface_speed_preset_has_no_spindle_speed_and_names_what_it_has(self):
        # a turning preset cutting at constant surface speed - the spindle_speed value has nowhere
        # to go, and the refusal must name tool_surfaceSpeed rather than a mill-only parameter
        p, avail = _speed_param(_preset_with(["tool_surfaceSpeed", "tool_feedCutting"]))
        assert p is None and avail == ["tool_surfaceSpeed"]


# ── the from_type vocabulary comes from the bundled Fusion360 sample libraries ──────────────────

class _AssetURL:
    def __init__(self, leaf):
        self.leafName = leaf
    def toString(self):
        return "systemlibraryroot://Samples/" + self.leafName


class _SampleAssets:
    """ToolLibraries over the bundled Fusion360 sample assets: leaf name -> the tool types it holds.
    Each toolLibraryAtURL is a CLOUD FETCH live (~1.4-6.7s each, measured), so `fetched` records
    them - a test can then pin that a lookup stopped early instead of walking all five."""
    def __init__(self, by_leaf):
        self._by_leaf = by_leaf
        self.fetched = []
    def urlByLocation(self, loc):
        return _AssetURL("Fusion360")
    def childAssetURLs(self, url):
        return [_AssetURL(leaf) for leaf in self._by_leaf]
    def toolLibraryAtURL(self, url):
        self.fetched.append(url.leafName)
        return _SrcLib([_Tool(ty, tool_type=ty) for ty in self._by_leaf.get(url.leafName, [])])


def _install_samples(monkeypatch, by_leaf):
    from types import SimpleNamespace
    import adsk.cam as _c
    import adsk.core as _core
    assets = _SampleAssets(by_leaf)
    mgr = SimpleNamespace(libraryManager=SimpleNamespace(toolLibraries=assets))
    monkeypatch.setattr(_c.CAMManager, "get", lambda: mgr)
    # a type-map entry stores the library's url STRING; _source_tool turns it back into a URL
    monkeypatch.setattr(_core.URL, "create", lambda s: _AssetURL(s.rsplit("/", 1)[-1]))
    monkeypatch.setattr(ct, "_type_map_cache", None)      # the map is cached across calls
    monkeypatch.setattr(ct, "_library_cache", {})         # as are the fetched libraries
    return assets


# A miniature stand-in for the bundled Fusion360 sample assets - a handful of leaf names and the
# tool types behind them, shaped like the real set (an Inch library repeating its Metric twin, and
# a Hole Making Tools (Inch) that adds 'center drill') so the walk can be pinned without them.
_SAMPLE_ASSETS = {
    "Milling Tools (Metric)": ["flat end mill", "ball end mill"],
    "Milling Tools (Inch)": ["flat end mill"],
    "Hole Making Tools (Metric)": ["drill"],
    "Hole Making Tools (Inch)": ["drill", "center drill"],
    "Cutting Tools (Metric)": ["laser cutter"],
    "Turning Tools (Metric)": ["turning general", "turning threading"],
    "Turning Tools (Inch)": ["turning general"],
    "Probes": ["probe"],
}


class TestSampleLibraryFetchCost:
    """Each sample library is a cloud round-trip (~13s for all five, measured live) against a 30s
    handler budget, so a lookup must read only as far as it needs."""

    def test_a_type_in_the_first_library_reads_only_that_library(self, monkeypatch):
        # ONE fetch, not one per read: the type walk and _source_tool share the cached library.
        assets = _install_samples(monkeypatch, _SAMPLE_ASSETS)
        tool, err = ct._sample_for_type("flat end mill")
        assert err is None and tool is not None
        assert assets.fetched == ["Milling Tools (Metric)"]

    def test_a_later_type_stops_at_the_library_holding_it(self, monkeypatch):
        assets = _install_samples(monkeypatch, _SAMPLE_ASSETS)
        _tool, err = ct._sample_for_type("drill")
        assert err is None
        assert "Turning Tools (Metric)" not in assets.fetched   # never reached

    def test_a_second_lookup_scans_no_further_libraries(self, monkeypatch):
        # The type MAP is cached, so a repeat lookup must not widen the walk - it still pays
        # _source_tool's own read of the one library it already knows about.
        assets = _install_samples(monkeypatch, _SAMPLE_ASSETS)
        ct._sample_for_type("flat end mill")
        before = set(assets.fetched)
        ct._sample_for_type("flat end mill")
        assert set(assets.fetched) == before

    def test_an_unknown_type_still_lists_the_whole_vocabulary(self, monkeypatch):
        # the early stop must not shrink the refusal's "Available types" to what happened to be read
        assets = _install_samples(monkeypatch, _SAMPLE_ASSETS)
        tool, err = ct._sample_for_type("no such tool")
        assert tool is None
        for expected in ("flat end mill", "drill", "turning general", "center drill"):
            assert expected in err


class TestSampleTypeMap:
    def test_turning_types_resolve_to_the_turning_sample_library(self, monkeypatch):
        # without the Turning sample library in the walk, add_tools[].from_type cannot clone a
        # turning tool at all - no turning type is in the vocabulary.
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        tmap = ct._build_type_map()
        assert "turning general" in tmap and "turning threading" in tmap
        url, index = tmap["turning general"]
        assert url.endswith("Turning Tools (Metric)") and index == 0

    def test_center_drill_comes_from_the_inch_hole_making_library(self, monkeypatch):
        # the one type no Metric sample library holds - without that Inch library in the walk it is
        # not cloneable at all.
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        url, index = ct._build_type_map()["center drill"]
        assert url.endswith("Hole Making Tools (Inch)") and index == 1

    def test_a_shared_type_keeps_its_metric_library(self, monkeypatch):
        # 'drill' is in both hole-making libraries; the Metric one is walked first and wins the key,
        # so adding the Inch library never re-points an existing type at an inch tool.
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        url, _ = ct._build_type_map()["drill"]
        assert url.endswith("Hole Making Tools (Metric)")

    def test_map_walks_only_the_named_libraries(self, monkeypatch):
        # Milling/Turning Inch add nothing their Metric twins lack, and Probes is not a cutting-tool
        # sample - none of the three is walked.
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        assert set(ct._build_type_map()) == {"flat end mill", "ball end mill", "drill",
                                             "center drill", "laser cutter", "turning general",
                                             "turning threading"}

    def test_a_turning_type_clones_through_the_add_path(self, monkeypatch):
        # _sample_for_type resolves 'turning general' through the same map to a real source tool.
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        src, serr = ct._sample_for_type("turning general")
        assert serr is None and src.desc == "turning general"

    def test_unknown_type_lists_the_turning_types_too(self, monkeypatch):
        _install_samples(monkeypatch, _SAMPLE_ASSETS)
        src, serr = ct._sample_for_type("banana mill")
        assert src is None and "turning general" in serr


# ── presets on an EXISTING tool: add_preset / remove_preset ─────────────────────────────────────

def _tool_with_presets(desc, names=(), **params):
    t = _Tool(desc, **params)
    t.presets = _Presets(names, owner=t)
    return t


class TestAddPreset:
    def test_adds_a_named_preset_with_its_values(self, monkeypatch):
        tool = _tool_with_presets("EM")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        out = _payload(ct.handler(action="add_preset", scope="document", tool=0,
                                  preset={"name": "Alu 6061", "spindle_speed": 12000, "feed": 900}))
        assert _preset_names(tool) == ["Alu 6061"]
        p = tool.presets.item(0)
        assert p.parameters.itemByName("tool_spindleSpeed").expression == "12000"
        assert p.parameters.itemByName("tool_feedCutting").expression == "900"
        assert out["preset_index"] == 0 and out["preset_count"] == 1
        assert out["presets"] == ["Alu 6061"]
        assert tgt.updated and tgt.persisted == 0        # document scope commits via update_tool

    def test_shared_scope_persists_the_library(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Steel"])
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=False))
        out = _payload(ct.handler(action="add_preset", scope="cloud", library="L", tool=0,
                                  preset={"name": "Alu 6061"}))
        assert tgt.persisted == 1 and tgt.updated == []
        assert out["presets"] == ["Steel", "Alu 6061"]   # appended after the existing preset

    def test_duplicate_name_refused(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Alu 6061"])
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "alu 6061"})       # same name, different case
        assert res["isError"] is True and "already has a preset" in res["message"]
        assert tool.presets.count == 1

    def test_detached_preset_that_never_lands_in_the_collection_errors(self, monkeypatch):
        # add() hands back a preset that is NOT in the collection - the values apply to an object
        # nobody can reach again, so a bare 'added' would be a lie.
        class _Detached(_Presets):
            def add(self):
                return _Preset()

        tool = _tool_with_presets("EM")
        tool.presets = _Detached()
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "did not take" in res["message"]

    def test_name_that_does_not_land_errors_and_rolls_the_preset_back(self, monkeypatch):
        # The assigned name is read back, so a name that does not store is an error and the empty
        # preset add() created is not left behind.
        class _Unnamed(_Preset):
            def __setattr__(self, key, value):
                if key == "name" and getattr(self, "name", None) is not None:
                    return                              # the assignment is accepted and ignored
                object.__setattr__(self, key, value)

        class _UnnamedPresets(_Presets):
            def add(self):
                p = _Unnamed(); self._p.append(p); return p

        tool = _tool_with_presets("EM")
        tool.presets = _UnnamedPresets()
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "did not land" in res["message"]
        assert tool.presets.count == 0                  # rolled back, no half-populated preset
        assert tgt.updated == [] and tgt.persisted == 0

    def test_missing_spindle_speed_parameter_rolls_back_and_errors(self, monkeypatch):
        class _Bare(_Preset):
            def __init__(self, name=""):
                super().__init__(name)
                self.parameters = _Params({})

        class _BarePresets(_Presets):
            def add(self):
                p = _Bare(); self._p.append(p); return p

        tool = _tool_with_presets("EM")
        tool.presets = _BarePresets()
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "spindle_speed": 9000})
        assert res["isError"] is True and "tool_spindleSpeed" in res["message"]
        assert tool.presets.count == 0                  # nothing half-built survives

    def test_persisted_library_without_the_preset_bites(self, monkeypatch):
        # update_tool/updateToolLibrary returning is not proof - the tool re-read from the library
        # has no such preset, so the add did not persist.
        tool = _tool_with_presets("EM")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        tgt.reread_preset_names = lambda index: []
        res = ct.handler(action="add_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "did not reach the library" in res["message"]

    def test_requires_a_name(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0, preset={"spindle_speed": 900})
        assert res["isError"] is True and "'name'" in res["message"]

    def test_bad_tool_index(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=4, preset={"name": "Alu"})
        assert res["isError"] is True and "0..0" in res["message"]

    def test_document_scope_note_claims_presence_not_persistence(self, monkeypatch):
        # a re-read of the document library returns the document's own unsaved state, so the note
        # may claim the preset is there - not that anything was stored.
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=True))
        out = _payload(ct.handler(action="add_preset", scope="document", tool=0,
                                  preset={"name": "Alu"}))
        assert "persist" not in out["note"].lower() and "doc_save" in out["note"]

    def test_document_scope_add_preset_never_claims_storage_though_the_refetch_reads(self, monkeypatch):
        # Readable document refetch: the preset is confirmed PRESENT, never stored - weak flag, and
        # the note carries the document rung rather than the persistence claim.
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=True))
        out = _payload(ct.handler(action="add_preset", scope="document", tool=0,
                                  preset={"name": "Alu"}))
        assert out["presets"] == ["Alu"]                 # the effect still reports
        assert out["verified_in_memory_only"] is True
        assert "present, not that it was stored" in out["note"] and "doc_save" in out["note"]
        assert "persist" not in out["note"].lower()

    def test_shared_scope_note_claims_persistence(self, monkeypatch):
        # the shared branch round-trips through the library url, which does prove the write stored
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=False))
        out = _payload(ct.handler(action="add_preset", scope="cloud", library="L", tool=0,
                                  preset={"name": "Alu"}))
        assert "persisted" in out["note"] and out["verified_in_memory_only"] is False

    def test_unreadable_refetch_drops_the_persistence_claim(self, monkeypatch):
        # The library could not be re-read, so nothing proves the preset reached storage - the
        # payload keeps the in-memory verdict and names that weaker basis.
        tgt = _install(monkeypatch, _Target(tools=[_tool_with_presets("EM")], is_document=False))
        tgt._fresh = lambda: None
        out = _payload(ct.handler(action="add_preset", scope="cloud", library="L", tool=0,
                                  preset={"name": "Alu"}))
        assert out["presets"] == ["Alu"]                 # the in-memory read still reports it
        assert out["verified_in_memory_only"] is True
        assert "persisted" not in out["note"] and "in-memory" in out["note"]


# ── preset VALUES: a CAM parameter stores an expression it cannot evaluate and still reads a ────
# ── finite 0.0 back, so setting one is only done when .error is clear AND the value read back ───
# ── agrees with what was asked for. ─────────────────────────────────────────────────────────────

class _BrokenParam(_Param):
    """A CAM parameter whose expression is stored verbatim but never evaluates: .error carries the
    platform's message and .value stays where it was."""
    error = "Failed to evaluate expression."
    warning = ""

    @property
    def expression(self):
        return self._expr

    @expression.setter
    def expression(self, v):
        self._expr = v


class _StuckParam(_Param):
    """A parameter that accepts the expression, reports no error, and evaluates to something else."""
    error = ""
    warning = ""

    @property
    def expression(self):
        return self._expr

    @expression.setter
    def expression(self, v):
        self._expr = v
        self.value = _Val(0.0)


def _preset_param_tool(cls, pname):
    """A tool whose presets carry `pname` as an instance of the misbehaving parameter class."""
    tool = _tool_with_presets("EM")

    class _Presets2(_Presets):
        def add(self):
            p = super().add()
            p.parameters._d[pname] = cls(pname, "0")
            return p

    tool.presets = _Presets2(owner=tool)
    return tool


class TestPresetValues:
    def test_expression_that_does_not_evaluate_errors_and_rolls_back(self, monkeypatch):
        tool = _preset_param_tool(_BrokenParam, "tool_spindleSpeed")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        # the expression opens with a number, so it clears the spec gate and reaches the parameter,
        # which stores it verbatim and reads a finite value back - only .error reveals the break
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "spindle_speed": "900 * NoSuchParamXyz"})
        assert res["isError"] is True and "failed to evaluate" in res["message"]
        assert tool.presets.count == 0
        assert tgt.updated == [] and tgt.persisted == 0

    def test_value_that_reads_back_different_errors_and_rolls_back(self, monkeypatch):
        tool = _preset_param_tool(_StuckParam, "tool_feedCutting")
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "feed": 900})
        assert res["isError"] is True and "did not land" in res["message"]
        assert "mm/min" in res["message"]
        assert tool.presets.count == 0
        assert tgt.updated == [] and tgt.persisted == 0

    def test_units_carrying_expression_is_accepted_and_stored_verbatim(self, monkeypatch):
        # a bare number is a fixed unit whatever the document uses, so a string carries its own. The
        # read-back must judge such a value by what the parameter EVALUATED to - judging it by
        # comparing against the text would fail this add, so _payload's no-error gate is the assert.
        tool = _tool_with_presets("EM")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        out = _payload(ct.handler(action="add_preset", scope="document", tool=0,
                                  preset={"name": "Alu", "feed": "35in/min"}))
        assert out["preset_count"] == 1 and out["presets"] == ["Alu"]
        p = tool.presets.item(0).parameters.itemByName("tool_feedCutting")
        assert p.expression == "35in/min"        # stored verbatim, units and all

    def test_turning_preset_without_spindle_speed_names_its_surface_speed(self, monkeypatch):
        # a turning preset cutting at constant surface speed carries no tool_spindleSpeed - the
        # refusal names what it DOES carry instead of asserting the mill parameter.
        tool = _tool_with_presets("Turning General", preset_params=_TURNING_CUTTING_DATA)
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Steel", "spindle_speed": 400})
        assert res["isError"] is True
        assert "tool_surfaceSpeed" in res["message"] and "tool_spindleSpeed" in res["message"]
        assert tool.presets.count == 0                  # rolled back

    def test_turning_preset_takes_a_feed(self, monkeypatch):
        # the same turning preset still carries a cutting feed - only the speed has nowhere to go
        tool = _tool_with_presets("Turning General", preset_params=_TURNING_CUTTING_DATA)
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        _payload(ct.handler(action="add_preset", scope="document", tool=0,
                            preset={"name": "Steel", "feed": 250}))
        assert tool.presets.item(0).parameters.itemByName("tool_feedCutting").expression == "250"

    def test_a_mistyped_key_is_refused_not_silently_dropped(self, monkeypatch):
        tool = _tool_with_presets("EM")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "spindel_speed": 12000})
        assert res["isError"] is True and "spindel_speed" in res["message"]
        assert tool.presets.count == 0                  # nothing was created

    def test_a_value_that_is_not_a_number_or_expression_is_refused(self, monkeypatch):
        tool = _tool_with_presets("EM")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "feed": "fast"})
        assert res["isError"] is True and "'fast'" in res["message"]
        assert "does not open with a number" in res["message"]     # refused, never handed to a param
        assert tool.presets.count == 0

    def test_a_non_scalar_value_is_refused(self, monkeypatch):
        tool = _tool_with_presets("EM")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="add_preset", scope="document", tool=0,
                         preset={"name": "Alu", "spindle_speed": [12000]})
        assert res["isError"] is True and "rpm" in res["message"]
        assert tool.presets.count == 0

    def test_creation_time_presets_are_validated_too(self, monkeypatch):
        # add_tools[].presets[] runs the same spec gate, before any preset is created
        tgt = _install(monkeypatch)
        res = ct.handler(action="add", scope="cloud", library="L",
                         add_tools=[{"from_type": "drill", "presets": [{"feed_rate": 900}]}])
        assert res["isError"] is True and "feed_rate" in res["message"]
        assert len(tgt.tools) == 2 and tgt.persisted == 0


class TestRemovePreset:
    def test_removes_the_named_preset_by_index(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Steel", "Alu 6061", "Brass"])
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        out = _payload(ct.handler(action="remove_preset", scope="document", tool=0,
                                  preset={"name": "Alu 6061"}))
        assert _preset_names(tool) == ["Steel", "Brass"]
        assert out["removed_index"] == 1 and out["preset_count"] == 2
        assert out["presets"] == ["Steel", "Brass"] and tgt.updated

    def test_document_scope_removal_never_claims_storage_though_the_refetch_reads(self, monkeypatch):
        # Readable document refetch: the removal is confirmed gone from the document library, which
        # is presence, not storage - weak flag, document-rung note, effect still reported.
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM", ["Alu"])], is_document=True))
        out = _payload(ct.handler(action="remove_preset", scope="document", tool=0,
                                  preset={"name": "Alu"}))
        assert out["removed_index"] == 0 and out["presets"] == []   # the effect still reports
        assert out["verified_in_memory_only"] is True
        assert "present, not that it was stored" in out["note"] and "doc_save" in out["note"]
        assert "persist" not in out["note"].lower()

    def test_matches_case_insensitively(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Alu 6061"])
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        _payload(ct.handler(action="remove_preset", scope="document", tool=0,
                            preset={"name": "ALU 6061"}))
        assert _preset_names(tool) == []

    def test_a_name_is_never_a_wildcard_pattern(self, monkeypatch):
        # ToolPresets.itemsByName reads '*' as a wild-card; the preset NAME must not, or 'Alu*'
        # would target 'Alu 6061' instead of the preset actually called 'Alu*'.
        tool = _tool_with_presets("EM", ["Alu 6061", "Alu*"])
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        out = _payload(ct.handler(action="remove_preset", scope="document", tool=0,
                                  preset={"name": "Alu*"}))
        assert out["removed_index"] == 1 and _preset_names(tool) == ["Alu 6061"]

    def test_ambiguous_name_refused_with_candidates(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Alu", "Steel", "alu"])
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True
        assert "0, 2" in res["message"]                  # both candidate indices named
        assert _preset_names(tool) == ["Alu", "Steel", "alu"]     # nothing removed
        assert tgt.updated == [] and tgt.persisted == 0

    def test_unknown_name_lists_what_the_tool_has(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Steel", "Brass"])
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True
        assert "Steel, Brass" in res["message"] and tool.presets.count == 2

    def test_no_presets_at_all_says_none(self, monkeypatch):
        tool = _tool_with_presets("EM")
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "(none)" in res["message"]

    def test_remove_reporting_failure_errors(self, monkeypatch):
        # ToolPresets.remove returns false for an unsuccessful deletion - never report it as removed.
        class _Refuses(_Presets):
            def remove(self, index):
                return False

        tool = _tool_with_presets("EM", ["Alu"])
        tool.presets = _Refuses(["Alu"])
        _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "reported failure" in res["message"]

    def test_survivor_of_the_same_name_errors(self, monkeypatch):
        # remove() claims success but the preset is still on the tool - a lying delete, not an ok.
        class _NoOp(_Presets):
            def remove(self, index):
                return True

        tool = _tool_with_presets("EM")
        tool.presets = _NoOp(["Alu"])
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "did not take" in res["message"]
        assert tgt.updated == [] and tgt.persisted == 0

    def test_persisted_library_that_still_holds_it_bites(self, monkeypatch):
        tool = _tool_with_presets("EM", ["Alu"])
        tgt = _install(monkeypatch, _Target(tools=[tool], is_document=True))
        tgt.reread_preset_names = lambda index: ["Alu"]
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset={"name": "Alu"})
        assert res["isError"] is True and "did not reach the library" in res["message"]

    def test_preset_must_be_an_object(self, monkeypatch):
        _install(monkeypatch, _Target(tools=[_tool_with_presets("EM", ["Alu"])], is_document=True))
        res = ct.handler(action="remove_preset", scope="document", tool=0, preset="Alu")
        assert res["isError"] is True and "'preset' must be an object" in res["message"]


class TestCreationTimePresetName:
    def test_add_tools_preset_name_is_applied(self, monkeypatch):
        # the creation-time preset path and add_preset share one value applier, so a named preset
        # requested at creation carries that name.
        tgt = _install(monkeypatch)
        _payload(ct.handler(action="add", scope="cloud", library="L",
                            add_tools=[{"from_type": "drill",
                                        "presets": [{"name": "Alu 6061", "spindle_speed": 8000}]}]))
        built = tgt.tools[-1]
        assert _preset_names(built) == ["Alu 6061"]
        assert built.presets.item(0).parameters.itemByName("tool_spindleSpeed").expression == "8000"
