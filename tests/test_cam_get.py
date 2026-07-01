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
    monkeypatch.setattr(cg, "_get_cam", lambda: (object(), None))   # a CAM product
    monkeypatch.setattr(cg, "_slice_setups", lambda cam, setup: (
        {"setup_count": 2, "setups": [{"name": "Setup1", "operation_count": 3}]}, None))
    monkeypatch.setattr(cg, "_slice_operations", lambda cam, setup: ({"operations": []}, None))
    monkeypatch.setattr(cg, "_slice_references", lambda cam, setup: ({"references": []}, None))
    monkeypatch.setattr(cg, "_slice_nc_programs", lambda cam: ({"nc_programs": []}, None))
    monkeypatch.setattr(cg, "_slice_time", lambda cam, setup: ({"total_minutes": 12}, None))
    monkeypatch.setattr(cg, "_slice_tools", lambda cam: ({"tools": []}, None))
    monkeypatch.setattr(cg, "_slice_library",
                        lambda cam, scope, library, tool_type: ({"tool_count": 0, "tools": []}, None))
    monkeypatch.setattr(cg, "_slice_templates",
                        lambda cam, loc, url, depth: ({"node_count": 0, "tree": {}}, None))


class TestDefaultSlice:
    def test_default_returns_setups_only(self, stub_slices):
        out = _payload(cg.handler())
        assert "setups" in out and "setup_count" in out
        # the heavy slices must be absent by default (anti-flood)
        for k in ("operations", "references", "nc_programs", "time", "tools"):
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
        monkeypatch.setattr(cg, "_get_cam", lambda: (object(), None))
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
        monkeypatch.setattr(cg, "_get_cam", lambda: (object(), None))
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
        monkeypatch.setattr(cg, "_get_cam", lambda: (None, "This document has no CAM (Manufacture) data."))
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

    def _fake_cam_read(self, monkeypatch, **handlers):
        ccom = load_tool("_cam_common")
        for name, fn in handlers.items():
            monkeypatch.setattr(ccom, name, fn)

    def _ok(self, payload):
        return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}

    def test_operations_capped_and_flagged(self, monkeypatch):
        big = {"setups": [{"setup": "S", "operations": [
            {"name": f"Op{i}", "state": "valid"} for i in range(cg._OPERATIONS_CAP + 50)]}]}
        self._fake_cam_read(monkeypatch,
                            get_cam_operations_handler=lambda setup="": self._ok(big))
        out, err = cg._slice_operations(object(), "")
        assert err is None
        assert len(out["setups"][0]["operations"]) == cg._OPERATIONS_CAP and out["truncated"] is True

    def test_nc_programs_summarizes_post_parameters(self, monkeypatch):
        ncp = {"nc_programs": [{"name": "Op1", "machine": "M",
                                "post_parameters": [{"name": f"p{i}"} for i in range(65)]}]}
        self._fake_cam_read(monkeypatch, get_nc_programs_handler=lambda: self._ok(ncp))
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


class TestNormalizeInclude:
    def test_comma_string(self):
        assert cg._normalize_include("operations, time") == ["operations", "time"]

    def test_list_lowercased(self):
        assert cg._normalize_include(["Operations", "TIME"]) == ["operations", "time"]

    def test_none_empty(self):
        assert cg._normalize_include(None) == [] and cg._normalize_include("") == []
