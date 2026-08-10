"""Tests for `cam_get` — the CAM rich read (setups default + include= deeper slices).

Same fixture-based pattern as test_design_get.py (CLAUDE.md "Tests"): stub
the _slice_* SEAMS and assert the ROUTER's composition (default = setups orientation only; each include=
adds its slice; the note advertises the rest; unknown include + no-CAM guards). The slice→source-handler
delegation is proven by live validation, not by mocking 6 handlers' internals.
"""

import json

import pytest

from conftest import load_tool, error_message

cg = load_tool("cam_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def stub_slices(monkeypatch):
    monkeypatch.setattr(cg, "get_cam", lambda: (object(), None))   # a CAM product
    monkeypatch.setattr(cg, "_slice_setups", lambda cam, setup: (
        {"setup_count": 2, "setups": [{"name": "Setup1", "operation_count": 3}]}, None))
    monkeypatch.setattr(cg, "_slice_operations", lambda cam, setup: ({"operations": []}, None))
    monkeypatch.setattr(cg, "_slice_references", lambda cam, setup: ({"references": []}, None))
    monkeypatch.setattr(cg, "_slice_nc_programs", lambda cam: ({"nc_programs": []}, None))
    monkeypatch.setattr(cg, "_slice_time", lambda cam, setup: ({"total_minutes": 12}, None))
    monkeypatch.setattr(cg, "_slice_tools", lambda cam: ({"tools": []}, None))
    monkeypatch.setattr(cg, "_slice_library",
                        lambda cam, scope, library, tool_type: ({"tool_count": 0, "tools": []}, None))
    monkeypatch.setattr(cg, "_slice_library_types", lambda cam: ({"type_count": 0, "types": []}, None))
    monkeypatch.setattr(cg, "_slice_machines",
                        lambda cam, vendor, machine_type: ({"count": 0, "machines": []}, None))
    monkeypatch.setattr(cg, "_slice_templates",
                        lambda cam, loc, url, depth: ({"node_count": 0, "tree": {}}, None))
    monkeypatch.setattr(cg, "_slice_inspection",
                        lambda cam, measure, max_results, units: (
                            {"available": False, "measure_count": 0, "measures": []}, None))


class TestDefaultSlice:
    def test_default_returns_setups_only(self, stub_slices):
        out = _payload(cg.handler())
        assert "setups" in out and "setup_count" in out
        # the heavy slices must be absent by default (anti-flood)
        for k in ("operations", "references", "nc_programs", "time", "tools", "library_types",
                  "inspection"):
            assert k not in out

    def test_default_note_advertises_remaining(self, stub_slices):
        out = _payload(cg.handler())
        assert "include=" in out["note"]


class TestIncludeSlices:
    @pytest.mark.parametrize("slice_name,key", [
        ("operations", "operations"),
        ("references", "references"),
        ("nc_programs", "nc_programs"),
        ("time", "time"),
        ("tools", "tools"),
    ])
    def test_include_adds_the_slice(self, stub_slices, slice_name, key):
        out = _payload(cg.handler(include=[slice_name]))
        assert key in out

    def test_setup_filter_passes_through(self, monkeypatch, stub_slices):
        seen = {}
        monkeypatch.setattr(cg, "_slice_operations",
                            lambda cam, setup: (seen.update(setup=setup) or {"operations": []}, None))
        cg.handler(include=["operations"], setup="Setup1")
        assert seen["setup"] == "Setup1"

    def test_multiple_includes(self, stub_slices):
        out = _payload(cg.handler(include=["operations", "time"]))
        assert "operations" in out and "time" in out


class TestCamPointers:
    """`_cam_pointers` - the actionable breadcrumb: a present, actionable CAM state names the tool that
    resolves it (stale/ungenerated toolpaths -> cam_generate; out-of-date machine -> cam_edit_setup)."""

    def test_stale_ops_point_at_cam_generate(self):
        p = cg._cam_pointers([{"op_states": {"out_of_date": 2, "no_toolpath": 3}}])
        assert "cam_generate" in p["toolpaths"] and "5" in p["toolpaths"]   # 2 + 3 summed

    def test_out_of_date_machine_points_at_edit_setup(self):
        p = cg._cam_pointers([{"op_states": {}, "machine_out_of_date": True}])
        assert "cam_edit_setup" in p["machine"]

    def test_clean_setup_gets_no_pointers(self):
        # all valid, machine current -> nothing to point at (no noise).
        p = cg._cam_pointers([{"op_states": {"suppressed": 4}, "machine_out_of_date": False}])
        assert p == {}

    def test_sums_across_setups(self):
        p = cg._cam_pointers([{"op_states": {"out_of_date": 2}}, {"op_states": {"no_toolpath": 1}}])
        assert "3" in p["toolpaths"]

    def test_router_emits_pointers_on_stale_default(self, monkeypatch):
        monkeypatch.setattr(cg, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(cg, "_slice_setups", lambda cam, setup: (
            {"setup_count": 1, "setups": [{"name": "S", "op_states": {"out_of_date": 6},
                                           "machine_out_of_date": True}]}, None))
        out = _payload(cg.handler())
        assert "cam_generate" in out["pointers"]["toolpaths"]
        assert "cam_edit_setup" in out["pointers"]["machine"]


class TestOrientationDedup:
    """Content-aware de-dup: the orientation block STAYS on a deep call, but a fact the included slice
    restates per-op is dropped from the orientation copy. The orientation context an agent needs to act
    on a cold jump-in (machine, op_states, names) is preserved."""

    @pytest.fixture
    def stub_with_reasons(self, monkeypatch):
        monkeypatch.setattr(cg, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(cg, "_slice_setups", lambda cam, setup: ({"setup_count": 1, "setups": [
            {"name": "Op1", "machine": "Haas", "op_states": {"out_of_date": 2},
             "invalidation_reasons": ["Design changed: WCS origin"]}]}, None))
        monkeypatch.setattr(cg, "_slice_operations", lambda cam, setup: ({"operations": []}, None))
        monkeypatch.setattr(cg, "_slice_time", lambda cam, setup: ({"total_minutes": 5}, None))

    def test_default_keeps_setup_invalidation_reasons(self, stub_with_reasons):
        out = _payload(cg.handler())
        assert "invalidation_reasons" in out["setups"][0]

    def test_operations_drops_setup_reasons_keeps_context(self, stub_with_reasons):
        out = _payload(cg.handler(include=["operations"]))
        s = out["setups"][0]
        assert "invalidation_reasons" not in s          # restated per-op -> dropped from orientation
        assert s["machine"] == "Haas" and "op_states" in s and s["name"] == "Op1"   # context preserved

    def test_unrelated_include_keeps_setup_reasons(self, stub_with_reasons):
        # the time slice does NOT restate invalidation reasons, so they stay
        out = _payload(cg.handler(include=["time"]))
        assert "invalidation_reasons" in out["setups"][0]


class TestGuards:
    def test_unknown_include_errors(self, stub_slices):
        res = cg.handler(include=["bogus"])
        assert "bogus" in error_message(res).lower() or "unknown" in error_message(res).lower()

    def test_no_cam_data_guard(self, monkeypatch):
        monkeypatch.setattr(cg, "get_cam", lambda: (None, "This document has no CAM (Manufacture) data."))
        res = cg.handler()
        assert "cam" in error_message(res).lower()


class TestOperationRazor:
    """Keeping operation rows terse (_common.terse + _OP_NOISE)."""

    def test_healthy_op_collapses(self):
        op = cg.terse({"name": "Face1", "tool": "T", "strategy": "face", "state": "valid",
                       "has_toolpath": True, "toolpath_valid": True, "is_generating": False,
                       "is_suppressed": False, "is_optional": False, "has_warning": False,
                       "has_error": False, "is_out_of_date": False}, cg._OP_NOISE)
        assert op == {"name": "Face1", "tool": "T", "strategy": "face", "state": "valid"}

    def test_abnormal_op_keeps_its_flags(self):
        op = cg.terse({"name": "X", "state": "invalid", "has_error": True, "is_suppressed": False,
                       "is_out_of_date": True}, cg._OP_NOISE)
        assert op["has_error"] is True and op["is_out_of_date"] is True
        assert "is_suppressed" not in op                # the boring false is dropped


class TestBounding:
    """A large CAM doc must not flood: operation rows capped + flagged; nc post_parameters summarized.
    The slices call read handlers in _cam_common, so patch the handler on that module (a plain
    monkeypatch.setattr - auto-restored, no sys.modules swap) to return the (big) ok() payload."""

    def _fake_cam_common(self, monkeypatch, **handlers):
        ccom = load_tool("_cam_common")
        for name, fn in handlers.items():
            monkeypatch.setattr(ccom, name, fn)

    def _ok(self, payload):
        return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}

    def test_operations_capped_and_flagged(self, monkeypatch):
        big = {"setups": [{"setup": "S", "operations": [
            {"name": f"Op{i}", "state": "valid"} for i in range(cg._OPERATIONS_CAP + 50)]}]}
        self._fake_cam_common(monkeypatch,
                            get_cam_operations_handler=lambda setup="": self._ok(big))
        out, err = cg._slice_operations(object(), "")
        assert err is None
        assert len(out["setups"][0]["operations"]) == cg._OPERATIONS_CAP and out["truncated"] is True

    def test_nc_programs_summarizes_post_parameters(self, monkeypatch):
        ncp = {"nc_programs": [{"name": "Op1", "machine": "M",
                                "post_parameters": [{"name": f"p{i}"} for i in range(65)]}]}
        self._fake_cam_common(monkeypatch, get_nc_programs_handler=lambda: self._ok(ncp))
        out, err = cg._slice_nc_programs(object())
        prog = out["nc_programs"][0]
        assert prog["post_parameter_count"] == 65 and "post_parameters" not in prog


class TestLibrarySlice:
    """include=['library'] = a tool-library catalog (the tools you can ADD), delegated to
    cam_edit_tools.read_library (the READ half of that tool); add/remove/edit stay on cam_edit_tools."""

    def test_router_includes_library_and_passes_scope(self, monkeypatch, stub_slices):
        # the router must route include=['library'] AND forward scope/library/tool_type to the slice.
        seen = {}
        monkeypatch.setattr(cg, "_slice_library",
                            lambda cam, scope, library, tool_type: (
                                seen.update(scope=scope, library=library, tool_type=tool_type)
                                or ({"tool_count": 1, "tools": [{"index": 0}]}, None)))
        out = _payload(cg.handler(include=["library"], scope="cloud", library="Shop", tool_type="ball"))
        assert out["library"]["tool_count"] == 1
        assert seen == {"scope": "cloud", "library": "Shop", "tool_type": "ball"}

    def test_slice_delegates_to_read_library(self, monkeypatch):
        # _slice_library unwraps cam_edit_tools.read_library's ok() payload (one read implementation).
        ctl = load_tool("cam_edit_tools")
        monkeypatch.setattr(ctl, "read_library",
                            lambda scope, library, tool_type: {
                                "isError": False,
                                "content": [{"type": "text", "text": json.dumps(
                                    {"tool_count": 2, "tools": [{"index": 0}, {"index": 1}]})}]})
        out, err = cg._slice_library(object(), "document", "", "")
        assert err is None and out["tool_count"] == 2

    def test_router_includes_library_types(self, monkeypatch, stub_slices):
        monkeypatch.setattr(cg, "_slice_library_types",
                            lambda cam: ({"type_count": 2, "types": ["ball end mill", "drill"]}, None))
        out = _payload(cg.handler(include=["library_types"]))
        assert out["library_types"]["types"] == ["ball end mill", "drill"]
        assert out["library_types"]["type_count"] == 2

    def test_library_types_slice_runs_cam_edit_tools_list_types(self, monkeypatch):
        # _slice_library_types runs cam_edit_tools' _do_list_types (imported, not copied): the REAL
        # sorted-type payload comes through, driven by that module's own _build_type_map seam.
        ctl = load_tool("cam_edit_tools")
        monkeypatch.setattr(ctl, "_build_type_map",
                            lambda: {"drill": ("u", 0), "ball end mill": ("u", 1),
                                     "chamfer mill": ("u", 2)})
        out, err = cg._slice_library_types(object())
        assert err is None
        assert out["types"] == ["ball end mill", "chamfer mill", "drill"]   # sorted, actual content
        assert out["type_count"] == 3

    def test_library_types_empty_map_surfaces_error(self, monkeypatch):
        ctl = load_tool("cam_edit_tools")
        monkeypatch.setattr(ctl, "_build_type_map", lambda: {})
        out, err = cg._slice_library_types(object())
        assert out is None and err["isError"] is True

    def test_router_includes_machines_and_passes_filters(self, monkeypatch, stub_slices):
        # include=['machines'] must route AND forward the vendor + machine_type filters to the slice.
        seen = {}
        monkeypatch.setattr(cg, "_slice_machines",
                            lambda cam, vendor, machine_type: (
                                seen.update(vendor=vendor, machine_type=machine_type)
                                or ({"count": 2, "machines": []}, None)))
        out = _payload(cg.handler(include=["machines"], vendor="Haas", machine_type="milling"))
        assert out["machines"]["count"] == 2
        assert seen == {"vendor": "Haas", "machine_type": "milling"}

    def test_machines_slice_delegates_to_read_machines(self, monkeypatch):
        # _slice_machines unwraps cam_edit_setup.read_machines' ok() payload (the read lives with the
        # machine resolver, its owner; cam_get is the wire surface).
        ces = load_tool("cam_edit_setup")
        seen = {}
        monkeypatch.setattr(ces, "read_machines",
                            lambda vendor, machine_type: (
                                seen.update(vendor=vendor, machine_type=machine_type)
                                or {"isError": False,
                                    "content": [{"type": "text", "text": json.dumps(
                                        {"count": 1, "machines": [{"name": "Haas VF-2"}]})}]}))
        out, err = cg._slice_machines(object(), "Haas", "milling")
        assert err is None and out["count"] == 1
        assert seen == {"vendor": "Haas", "machine_type": "milling"}

    def test_templates_slice_delegates_with_location(self, monkeypatch):
        # _slice_templates forwards location/url/depth to cam_templates' list engine and unwraps it.
        seen = {}
        ct = load_tool("cam_templates")
        monkeypatch.setattr(ct, "list_cam_templates_handler",
                            lambda location, url, max_depth: (
                                seen.update(location=location, url=url, max_depth=max_depth)
                                or {"isError": False, "content": [{"type": "text", "text": json.dumps(
                                    {"node_count": 3, "tree": {"folder": "Root"}})}]}))
        out, err = cg._slice_templates(object(), "local", "", 2)
        assert err is None and out["node_count"] == 3
        assert seen == {"location": "local", "url": "", "max_depth": 2}

    def test_templates_slice_defaults_location_and_depth(self, monkeypatch):
        # empty location/depth default to cloud / 4 (the engine's contract).
        seen = {}
        ct = load_tool("cam_templates")
        monkeypatch.setattr(ct, "list_cam_templates_handler",
                            lambda location, url, max_depth: (
                                seen.update(location=location, max_depth=max_depth)
                                or {"isError": False, "content": [{"type": "text", "text": "{}"}]}))
        cg._slice_templates(object(), "", "", 0)
        assert seen == {"location": "cloud", "max_depth": 4}


class TestDeepZoom:
    """One operation's detail: include=parameters/tool need 'operation'; params group by section; preset drills."""

    def test_parameters_requires_operation(self, stub_slices):
        res = cg.handler(include=["parameters"])           # no operation=
        assert "operation" in error_message(res).lower()

    def test_tool_requires_operation(self, stub_slices):
        res = cg.handler(include=["tool"])
        assert "operation" in error_message(res).lower()

    def test_grouped_visible_params_sections_and_filters(self):
        # a fake param collection: group sentinels open sections; invisible/disabled are dropped.
        class _P:
            def __init__(s, name, title, expr="", vis=True, en=True, val=None):
                s.name, s.title, s.expression, s.isVisible, s.isEnabled, s.value = name, title, expr, vis, en, val
        class _Coll:
            def __init__(s, items): s._i = items
            @property
            def count(s): return len(s._i)
            def item(s, i): return s._i[i]
        coll = _Coll([
            _P("group_feedspeed", "Feed & Speed", val=True),
            _P("tool_feedCutting", "Cutting Feedrate", "5252.1"),
            _P("tool_spindleSpeed", "Spindle Speed", "12000"),
            _P("hidden", "Hidden", "x", vis=False),          # dropped (not visible)
            _P("group_geometry", "Geometry", val=True),
            _P("boundaryOffset", "Additional Offset", "50mm"),
        ])
        g = cg._grouped_visible_params(coll)
        assert g["Feed & Speed"] == [
            {"name": "tool_feedCutting", "title": "Cutting Feedrate", "expression": "5252.1"},
            {"name": "tool_spindleSpeed", "title": "Spindle Speed", "expression": "12000"}]
        assert g["Geometry"][0]["name"] == "boundaryOffset"
        assert "hidden" not in str(g)                        # invisible param dropped


class TestDuplicateOperationName:
    """_cam_common.find_operation REFUSES a duplicated name, returning each duplicate's 'Setup / op'
    path as the available list - the deep-zoom miss error must word that as ambiguity naming the
    paths, never a plain not-found."""

    def test_parameters_duplicate_words_ambiguity_with_paths(self, monkeypatch, stub_slices):
        monkeypatch.setattr(cg, "find_operation",
                            lambda cam, name: (None, ["Setup1 / Drill1", "Setup2 / Drill1"]))
        msg = error_message(cg.handler(include=["parameters"], operation="Drill1"))
        assert "ambiguous" in msg.lower()
        assert "Setup1 / Drill1" in msg and "Setup2 / Drill1" in msg

    def test_tool_duplicate_words_ambiguity_with_paths(self, monkeypatch, stub_slices):
        monkeypatch.setattr(cg, "find_operation",
                            lambda cam, name: (None, ["Setup1 / Drill1", "Setup2 / Drill1"]))
        msg = error_message(cg.handler(include=["tool"], operation="Drill1"))
        assert "ambiguous" in msg.lower() and "Setup2 / Drill1" in msg

    def test_true_miss_stays_not_found_listing_names(self, monkeypatch, stub_slices):
        monkeypatch.setattr(cg, "find_operation",
                            lambda cam, name: (None, ["Face1", "Adaptive1"]))
        msg = error_message(cg.handler(include=["parameters"], operation="Drill1"))
        assert "ambiguous" not in msg.lower()
        assert "Face1" in msg and "Adaptive1" in msg


class _PresetColl:
    """A ToolPresets collection (count/item), the shape _slice_tool walks for preset names."""
    def __init__(self, items): self._i = items
    @property
    def count(self): return len(self._i)
    def item(self, i): return self._i[i]


class _Preset:
    def __init__(self, name, exprs):
        self.name = name
        self.parameters = _PresetColl(
            [type("PP", (), {"name": k, "expression": v})() for k, v in exprs.items()])


class TestToolSlicePresets:
    """include=['tool'] publishes the tool's preset NAMES + count, and 'preset' drills ONE preset's
    expressions. The names come off a count/item walk, so a tool holding several presets must
    report every one - and a miss must name what IS available."""

    def _wire(self, monkeypatch, presets):
        tool = type("T", (), {"description": "6mm flat", "presets": _PresetColl(presets)})()
        op = type("O", (), {"name": "Adaptive1", "tool": tool})()
        monkeypatch.setattr(cg, "find_operation", lambda cam, name: (op, []))
        return op

    def test_every_preset_name_is_published_with_its_count(self, monkeypatch):
        self._wire(monkeypatch, [_Preset("Alu roughing", {"tool_feedCutting": "3000 mm/min"}),
                                 _Preset("Steel finishing", {"tool_feedCutting": "800 mm/min"}),
                                 _Preset("Brass", {"tool_spindleSpeed": "14000"})])
        out, err = cg._slice_tool(object(), "Adaptive1", "")
        assert err is None
        assert out["preset_names"] == ["Alu roughing", "Steel finishing", "Brass"]
        assert out["preset_count"] == 3
        assert out["tool"] == "6mm flat"

    def test_preset_drill_returns_that_presets_expressions(self, monkeypatch):
        self._wire(monkeypatch, [_Preset("Alu roughing", {"tool_feedCutting": "3000 mm/min"}),
                                 _Preset("Steel finishing", {"tool_feedCutting": "800 mm/min",
                                                             "tool_spindleSpeed": "4500"})])
        out, err = cg._slice_tool(object(), "Adaptive1", "Steel finishing")
        assert err is None
        assert out["preset"] == {"name": "Steel finishing",
                                 "expressions": {"tool_feedCutting": "800 mm/min",
                                                 "tool_spindleSpeed": "4500"}}

    def test_preset_miss_names_the_available_presets(self, monkeypatch):
        self._wire(monkeypatch, [_Preset("Alu roughing", {}), _Preset("Steel finishing", {})])
        out, err = cg._slice_tool(object(), "Adaptive1", "Titanium")
        assert out is None
        msg = err["message"]
        assert "Titanium" in msg and "Alu roughing" in msg and "Steel finishing" in msg

    def test_a_tool_with_no_presets_reports_an_empty_list_not_a_miss(self, monkeypatch):
        self._wire(monkeypatch, [])
        out, err = cg._slice_tool(object(), "Adaptive1", "")
        assert err is None and out["preset_names"] == [] and out["preset_count"] == 0


class TestInspectionSlice:
    """include=['inspection'] = the recorded probing results, delegated to _cam_common's
    get_inspection_results_handler; 'measure'/'max_results'/'units' scope and bound it."""

    def test_router_includes_inspection_and_passes_the_scope(self, monkeypatch, stub_slices):
        seen = {}
        monkeypatch.setattr(cg, "_slice_inspection",
                            lambda cam, measure, max_results, units: (
                                seen.update(measure=measure, max_results=max_results, units=units)
                                or ({"available": True, "measure_count": 1}, None)))
        out = _payload(cg.handler(include=["inspection"], measure="0/1", max_results=25,
                                  units="in"))
        assert out["inspection"]["measure_count"] == 1
        assert seen == {"measure": "0/1", "max_results": 25, "units": "in"}

    def test_slice_delegates_to_the_cam_common_handler(self, monkeypatch):
        ccom = load_tool("_cam_common")
        seen = {}
        monkeypatch.setattr(ccom, "get_inspection_results_handler",
                            lambda measure, max_results, units: (
                                seen.update(measure=measure, max_results=max_results, units=units)
                                or {"isError": False, "content": [{"type": "text", "text": json.dumps(
                                    {"available": False, "measure_count": 0})}]}))
        out, err = cg._slice_inspection(object(), "", 0, "mm")
        assert err is None and out["available"] is False
        assert seen == {"measure": "", "max_results": 0, "units": "mm"}

    def test_slice_error_surfaces_from_the_router(self, monkeypatch, stub_slices):
        monkeypatch.setattr(cg, "_slice_inspection",
                            lambda cam, measure, max_results, units: (
                                None, cg.error("measure index 5 is out of range")))
        assert "out of range" in error_message(cg.handler(include=["inspection"], measure="5"))


class TestNormalizeInclude:
    def test_comma_string(self):
        assert cg._normalize_include("operations, time") == ["operations", "time"]

    def test_list_lowercased(self):
        assert cg._normalize_include(["Operations", "TIME"]) == ["operations", "time"]

    def test_none_empty(self):
        assert cg._normalize_include(None) == [] and cg._normalize_include("") == []
