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


class TestCmToUnit:
    def test_is_the_inverse_of_unit_to_cm(self):
        for u, f in common.UNIT_TO_CM.items():
            assert common.CM_TO_UNIT[u] == 1.0 / f

    def test_mm_is_ten_per_cm(self):
        assert common.CM_TO_UNIT["mm"] == 10.0
        assert common.CM_TO_UNIT["cm"] == 1.0


class TestPtxyz:
    class _Pt:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    def test_scales_and_rounds(self):
        p = self._Pt(1.0, 2.0, 3.0)
        assert common.ptxyz(p, 10.0) == {"x": 10.0, "y": 20.0, "z": 30.0}

    def test_none_point_is_none(self):
        assert common.ptxyz(None, 10.0) is None


class _Coll:
    def __init__(self, items):
        self._items = list(items)
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None
    def itemByName(self, name):
        for it in self._items:
            if it.name == name:
                return it
        return None


class TestResultBodies:
    def _feature(self, bodies):
        return type("F", (), {"bodies": _Coll(bodies)})()

    def test_empty_feature_bodies(self):
        assert common.result_bodies(self._feature([])) == []

    def test_collects_bodies_in_order(self):
        b1, b2 = type("B", (), {})(), type("B", (), {})()
        assert common.result_bodies(self._feature([b1, b2])) == [b1, b2]

    def test_filters_none_bodies(self):
        b = type("B", (), {})()
        assert common.result_bodies(self._feature([b, None])) == [b]

    def test_none_feature_is_safe(self):
        assert common.result_bodies(None) == []

    def test_unreadable_bodies_is_safe(self):
        class Bad:
            @property
            def bodies(self):
                raise RuntimeError("gone")
        assert common.result_bodies(Bad()) == []


class TestTargetSketch:
    def test_named_sketch_found(self):
        sk = type("Sk", (), {"name": "S1"})()
        comp = type("C", (), {"sketches": _Coll([sk])})()
        sketch, requested = common.target_sketch(comp, "S1")
        assert sketch is sk and requested == "S1"

    def test_named_sketch_not_found(self):
        comp = type("C", (), {"sketches": _Coll([])})()
        sketch, requested = common.target_sketch(comp, "Nope")
        assert sketch is None and requested == "Nope"

    def test_no_name_returns_most_recent(self):
        sk0 = type("Sk", (), {"name": "S0"})()
        sk1 = type("Sk", (), {"name": "S1"})()
        comp = type("C", (), {"sketches": _Coll([sk0, sk1])})()
        sketch, requested = common.target_sketch(comp, "")
        assert sketch is sk1 and requested == ""

    def test_no_name_no_sketches_is_none(self):
        comp = type("C", (), {"sketches": _Coll([])})()
        sketch, requested = common.target_sketch(comp, "")
        assert sketch is None and requested == ""


class TestResolveEntityRef:
    class _Curves:
        def __init__(self, lines=(), arcs=(), circles=()):
            self.sketchLines = _Coll(list(lines))
            self.sketchArcs = _Coll(list(arcs))
            self.sketchCircles = _Coll(list(circles))

    def _sketch(self):
        line = type("Line", (), {"name": "L0"})()
        return type("Sk", (), {
            "sketchCurves": self._Curves(lines=[line]),
            "sketchPoints": _Coll([type("Pt", (), {"name": "P0"})()]),
        })()

    def test_resolves_line_by_index(self):
        assert common.resolve_entity_ref(self._sketch(), "line:0").name == "L0"

    def test_resolves_point_by_index(self):
        assert common.resolve_entity_ref(self._sketch(), "point:0").name == "P0"

    def test_bad_type_is_none(self):
        assert common.resolve_entity_ref(self._sketch(), "spline:0") is None

    def test_out_of_range_is_none(self):
        assert common.resolve_entity_ref(self._sketch(), "line:9") is None

    def test_malformed_ref_is_none(self):
        assert common.resolve_entity_ref(self._sketch(), "line") is None


class TestOperations:
    def test_maps_every_verb_to_a_feature_operation_attribute_name(self):
        for key in ("new", "new_body", "join", "cut", "intersect"):
            assert common.OPERATIONS[key].endswith("FeatureOperation")


class TestMinDistance:
    """The one measureMinimumDistance core both measure tools share - a READ, so a failure is an
    error result, never a swallowed None."""

    def _install_mgr(self, monkeypatch, result=None, raises=False):
        class _Mgr:
            def measureMinimumDistance(self, a, b):
                if raises:
                    raise RuntimeError("boom")
                return result
        monkeypatch.setattr(common.app, "measureManager", _Mgr())

    def test_success_returns_result_and_no_error(self, monkeypatch):
        res = type("R", (), {"value": 1.0})()
        self._install_mgr(monkeypatch, result=res)
        mr, err = common.min_distance(object(), object())
        assert err is None and mr is res

    def test_measure_exception_is_surfaced_as_error(self, monkeypatch):
        self._install_mgr(monkeypatch, raises=True)
        mr, err = common.min_distance(object(), object())
        assert mr is None and err["isError"] is True and "failed" in err["message"].lower()

    def test_none_result_is_an_error_not_a_silent_none(self, monkeypatch):
        self._install_mgr(monkeypatch, result=None)
        mr, err = common.min_distance(object(), object())
        assert mr is None and err["isError"] is True
