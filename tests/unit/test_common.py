"""Unit tests for the shared tool helpers (tools/_common.py).

This module is the substrate every MCP tool imports — one response shape, one error contract, one
unit convention. If these drift, every tool drifts, so pin the contract explicitly.
"""

import json
from types import SimpleNamespace

from conftest import MakeComp, entity_proxy, load_tool

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


def _comp_with_sketches(name, sketch_names=()):
    # Like the live Component: NO allComponents attribute (that collection is a Design property),
    # so reading it here raises AttributeError exactly as adsk does.
    sks = [type("Sk", (), {"name": n})() for n in sketch_names]
    return type("C", (), {"name": name, "sketches": _Coll(sks)})()


def _design_with(root, subs, active=None):
    # allComponents lives on the DESIGN and is a counted collection (count/item), root included.
    return type("D", (), {"rootComponent": root, "activeComponent": active or root,
                          "allComponents": _Coll([root] + list(subs))})()


class TestAllComponents:
    def test_reads_the_collection_off_the_design_not_the_root(self):
        # Component has no allComponents in the live API - reading it off the root raises, safe()
        # swallows, and the walk silently degrades to [root], hiding every sub-component from
        # "design-wide" reads (observed live: a 3-component doc summarized as 0 sketches).
        root = _comp_with_sketches("Root")
        sub = _comp_with_sketches("Frame")
        assert common.all_components(_design_with(root, [sub])) == [root, sub]

    def test_falls_back_to_root_when_the_design_lacks_the_collection(self):
        root = _comp_with_sketches("Root")
        d = type("D", (), {"rootComponent": root, "activeComponent": root})()
        assert common.all_components(d) == [root]

    def test_no_root_is_empty(self):
        class D:
            @property
            def rootComponent(self):
                raise RuntimeError("no design")
        assert common.all_components(D()) == []


class TestResolveSketchDesignWide:
    def test_finds_a_sub_component_sketch_while_root_is_active(self):
        root = _comp_with_sketches("Root")
        sub = _comp_with_sketches("Frame", ["FrameSketch"])
        sk = common.resolve_sketch(_design_with(root, [sub]), "FrameSketch")
        assert sk is not None and sk.name == "FrameSketch"

    def test_active_component_searched_first_for_a_shared_name(self):
        # Two components each hold a "Profile" sketch; the ACTIVE component's instance must win -
        # it is where sketch_create just put the caller's sketch in the assembly workflow.
        root = _comp_with_sketches("Root", ["Profile"])
        sub = _comp_with_sketches("Frame", ["Profile"])
        d = _design_with(root, [sub], active=sub)
        assert common.resolve_sketch(d, "Profile") is sub.sketches.item(0)


class TestAllSketchNames:
    def test_spans_every_component(self):
        root = _comp_with_sketches("Root", ["Base"])
        sub = _comp_with_sketches("Frame", ["FrameSketch"])
        assert common.all_sketch_names(_design_with(root, [sub])) == ["Base", "FrameSketch"]


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


class TestResolveEntityRefs(TestResolveEntityRef):
    """The comma-separated list parser over resolve_entity_ref - the ONE 'entities' selector
    sketch_constrain's list kinds and sketch_move/sketch_copy share."""

    def test_an_empty_selector_yields_no_refs_and_no_error(self):
        ents, refs, err = common.resolve_entity_refs(self._sketch(), "  ")
        assert (ents, refs, err) == ([], [], None)

    def test_one_ref_resolves(self):
        ents, refs, err = common.resolve_entity_refs(self._sketch(), "line:0")
        assert [e.name for e in ents] == ["L0"] and refs == ["line:0"] and err is None

    def test_several_refs_keep_the_order_given_and_tolerate_spacing(self):
        ents, refs, err = common.resolve_entity_refs(self._sketch(), " point:0 , line:0 ")
        assert [e.name for e in ents] == ["P0", "L0"] and refs == ["point:0", "line:0"]
        assert err is None

    def test_the_first_ref_that_misses_is_named_with_the_legal_kinds(self):
        ents, refs, err = common.resolve_entity_refs(self._sketch(), "line:0,arc:7,line:0")
        assert ents is None and "'arc:7'" in err and "line/arc/circle" in err
        assert refs == ["line:0", "arc:7", "line:0"]     # kept, so a caller can echo what it got

    def test_the_field_name_in_the_error_is_the_caller_s(self):
        _e, _r, err = common.resolve_entity_refs(self._sketch(), "arc:7", field="targets")
        assert "in 'targets'" in err


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


# ── same_component / root_body_advisory: identity NEVER carries the comparison ───────────────

class _FakeDesignWithRoot:
    def __init__(self, root, occurrence_count=0):
        self._root = root
        self._occ = occurrence_count

    @property
    def rootComponent(self):
        # a FRESH wrapper on every read, as the platform hands back (measured: two reads of
        # design.rootComponent are different Python objects sharing one entityToken)
        self._root.occurrences = type("C", (), {"count": self._occ})()
        return entity_proxy(self._root)


def _root(name="Root", token="TOKEN:Root", bodies=0):
    comp = MakeComp(name=name, bodies=["B%d" % i for i in range(bodies)])
    comp.entityToken = token
    return comp


class TestSameComponent:
    def test_distinct_wrappers_of_one_component_are_the_same(self):
        c = _root()
        assert common.same_component(entity_proxy(c), entity_proxy(c)) is True

    def test_identity_still_short_circuits(self):
        c = _root()
        assert common.same_component(c, c) is True

    def test_different_tokens_are_different_components(self):
        assert common.same_component(_root(token="TOKEN:A"), _root(token="TOKEN:B")) is False

    def test_falls_back_to_name_when_a_token_cannot_be_read(self):
        a, b = _root(name="Sub", token=None), _root(name="Sub", token=None)
        assert common.same_component(a, b) is True

    def test_name_fallback_still_separates_different_names(self):
        a, b = _root(name="Sub", token=None), _root(name="Other", token=None)
        assert common.same_component(a, b) is False

    def test_none_is_never_the_same_component(self):
        assert common.same_component(None, _root()) is False
        assert common.same_component(_root(), None) is False


# ── the assembly-context walk + the cycle test the structural edits run on ──────────────────────


def _occ(path, component=None):
    """One occurrence record: the two attributes these helpers read."""
    return SimpleNamespace(fullPathName=path, component=component)


def _design_with_occurrences(*occs, root=None):
    root = root if root is not None else _root()
    root.allOccurrences = list(occs)
    return type("D", (), {"rootComponent": root})()


class TestAllOccurrences:
    def test_reads_the_root_components_assembly_walk(self):
        a, b = _occ("Frame:1"), _occ("Frame:1+Bolt:1")
        assert common.all_occurrences(_design_with_occurrences(a, b)) == [a, b]

    def test_no_design_is_empty(self):
        assert common.all_occurrences(None) == []

    def test_an_unreadable_root_is_empty_not_a_crash(self):
        class D:
            @property
            def rootComponent(self):
                raise RuntimeError("no design")
        assert common.all_occurrences(D()) == []


class TestOccurrencePaths:
    def test_returns_the_set_of_assembly_paths(self):
        d = _design_with_occurrences(_occ("Frame:1"), _occ("Frame:1+Bolt:1"))
        assert common.occurrence_paths(d) == {"Frame:1", "Frame:1+Bolt:1"}

    def test_an_unreadable_path_reads_as_empty_string_not_a_dropped_row(self):
        # a caller filters '' out; silently dropping the row would make a before/after diff report a
        # phantom new path instead.
        bad = SimpleNamespace()
        assert common.occurrence_paths(_design_with_occurrences(_occ("Frame:1"), bad)) == {
            "Frame:1", ""}

    def test_no_occurrences_is_an_empty_set(self):
        assert common.occurrence_paths(_design_with_occurrences()) == set()


class TestComponentContains:
    def test_a_component_contains_ITSELF_through_a_distinct_wrapper(self):
        # the identity trap: two wrappers of one component are different Python objects, so `outer is
        # inner` reads False and a self-nesting call would sail past the guard.
        comp = _root(name="Sub", token="TOKEN:Sub")
        assert common.component_contains(entity_proxy(comp), entity_proxy(comp)) is True

    def test_a_component_INSIDE_it_is_found_through_a_distinct_wrapper(self):
        inner = _root(name="Bolt", token="TOKEN:Bolt")
        outer = _root(name="Sub", token="TOKEN:Sub")
        outer.allOccurrences = [_occ("Sub:1+Bolt:1", component=entity_proxy(inner))]
        assert common.component_contains(outer, entity_proxy(inner)) is True

    def test_an_unrelated_component_is_not_contained(self):
        outer = _root(name="Sub", token="TOKEN:Sub")
        outer.allOccurrences = [_occ("Sub:1+Bolt:1", component=_root(name="Bolt",
                                                                    token="TOKEN:Bolt"))]
        assert common.component_contains(outer, _root(name="Frame", token="TOKEN:Frame")) is False

    def test_an_empty_or_unreadable_subtree_contains_nothing(self):
        outer = _root(name="Sub", token="TOKEN:Sub")
        outer.allOccurrences = []
        assert common.component_contains(outer, _root(name="X", token="TOKEN:X")) is False
        del outer.allOccurrences
        assert common.component_contains(outer, _root(name="X", token="TOKEN:X")) is False

    def test_none_contains_nothing(self):
        assert common.component_contains(None, _root()) is False


class TestRootBodyAdvisory:
    def test_fires_when_the_active_component_is_a_DIFFERENT_WRAPPER_of_the_root(self):
        # THE case an identity test gets wrong: comp and design.rootComponent denote the same
        # component but are different objects, so `comp is not d.rootComponent` reads True and the
        # advisory silently never fires at all.
        root = _root(bodies=1)
        design = _FakeDesignWithRoot(root)
        comp = design.rootComponent            # a wrapper, not the object design holds
        assert comp is not root
        assert "ROOT component" in common.root_body_advisory(design, comp)

    def test_silent_when_a_real_sub_component_is_active(self):
        design = _FakeDesignWithRoot(_root())
        assert common.root_body_advisory(design, _root(name="Sub", token="TOKEN:Sub")) == ""

    def test_silent_once_the_root_holds_several_bodies(self):
        root = _root(bodies=2)
        design = _FakeDesignWithRoot(root)
        assert common.root_body_advisory(design, design.rootComponent) == ""

    def test_silent_once_the_design_has_sub_components(self):
        root = _root(bodies=1)
        design = _FakeDesignWithRoot(root, occurrence_count=1)
        assert common.root_body_advisory(design, design.rootComponent) == ""

    def test_silent_without_a_component(self):
        assert common.root_body_advisory(_FakeDesignWithRoot(_root()), None) == ""
