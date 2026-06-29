"""Unit tests for the shared tool helpers (tools/_common.py).

This module is the substrate every MCP tool imports — one response shape, one error contract, one
unit convention. If these drift, every tool drifts, so pin the contract explicitly.
"""

import json

from conftest import load_tool

common = load_tool("_common")


class TestResponseBuilders:
    def test_ok_wraps_payload_as_json_text(self):
        res = common.ok({"a": 1, "b": "x"})
        assert res["isError"] is False
        assert json.loads(res["content"][0]["text"]) == {"a": 1, "b": "x"}

    def test_error_sets_flag_and_mirrors_message(self):
        res = common.error("boom")
        assert res["isError"] is True
        assert res["message"] == "boom"
        assert res["content"][0]["text"] == "boom"

    def test_underscore_aliases_are_gone(self):
        # the migration-era _ok/_error/_safe aliases were removed (single public spelling now).
        # Pin their ABSENCE so they can't silently creep back in.
        for legacy in ("_ok", "_error", "_safe", "_scale", "_target_component", "_UNIT_TO_CM"):
            assert not hasattr(common, legacy), f"_common should no longer export {legacy}"


class TestSafe:
    def test_returns_value(self):
        assert common.safe(lambda: 42) == 42

    def test_swallows_exception_returns_default(self):
        def boom():
            raise RuntimeError("x")
        assert common.safe(boom) is None
        assert common.safe(boom, "fallback") == "fallback"


class TestScale:
    def test_known_units(self):
        assert common.scale("mm") == 0.1
        assert common.scale("cm") == 1.0
        assert common.scale("in") == 2.54

    def test_default_is_mm(self):
        assert common.scale("") == 0.1
        assert common.scale(None) == 0.1

    def test_unknown_unit_is_none(self):
        assert common.scale("furlong") is None

    def test_case_and_whitespace_insensitive(self):
        assert common.scale("  MM ") == 0.1


class TestTargetComponent:
    def test_returns_active_component_when_set(self):
        active = object()
        d = type("D", (), {"activeComponent": active, "rootComponent": object()})()
        assert common.target_component(d) is active

    def test_falls_back_to_root_when_no_active(self):
        root = object()
        # activeComponent access raises -> safe() returns None -> fall back to root
        class D:
            rootComponent = root
            @property
            def activeComponent(self):
                raise RuntimeError("none active")
        assert common.target_component(D()) is root
