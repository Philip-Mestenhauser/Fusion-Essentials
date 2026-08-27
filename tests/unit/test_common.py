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


class TestReadFlag:
    """A BOOLEAN read: True / False / None. The whole point is that an unreadable flag is NOT a
    False - `safe(read, False)` at a set-then-read-back site lets a swallowed write pass a
    `now != wanted` gate whenever the wanted value is False and publishes that as confirmed."""

    def test_true_and_false_pass_through(self):
        assert common.read_flag(lambda: True) is True
        assert common.read_flag(lambda: False) is False

    def test_a_raising_getter_is_none_not_false(self):
        def boom():
            raise RuntimeError("3 : not available on this build")
        assert common.read_flag(boom) is None

    def test_a_flag_that_reads_none_is_none(self):
        assert common.read_flag(lambda: None) is None

    def test_a_truthy_non_bool_is_normalised_to_a_bool(self):
        # A SWIG getter can answer with an int; the caller compares against a bool, so 1 must not
        # come back as 1 (which `is True` would then reject).
        assert common.read_flag(lambda: 1) is True
        assert common.read_flag(lambda: 0) is False

    def test_the_unreadable_sentinel_never_escapes(self):
        def boom():
            raise RuntimeError("x")
        assert common.read_flag(boom) is not common._UNREADABLE


class TestAllComponentsRootFallback:
    def test_an_unreadable_component_list_still_walks_the_root(self):
        # allComponents reading as an empty/unreadable collection must not shrink a design-wide walk
        # to nothing: the root component is always there to walk, and returning [] would make every
        # by-name lookup built on this miss silently.
        root = MakeComp(name="Root")

        class _Blind:
            rootComponent = root

            @property
            def allComponents(self):
                raise RuntimeError("3 : cannot enumerate")

        assert common.all_components(_Blind()) == [root]

    def test_an_all_none_component_list_still_walks_the_root(self):
        root = MakeComp(name="Root")
        coll = SimpleNamespace(count=2, item=lambda i: None)
        d = SimpleNamespace(rootComponent=root, allComponents=coll)
        assert common.all_components(d) == [root]


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
    """A published POSITION is a measurement, and the tri-state is per POINT: every consumer
    navigates to / measures from the point as a whole, so one component that will not read makes
    the point unknown rather than a coordinate two thirds measured and one third invented."""

    class _Pt:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class _BlindZ:
        """A Point3D whose z read RAISES - a proxy that stopped answering one component."""
        x = 1.0
        y = 2.0

        @property
        def z(self):
            raise RuntimeError("point component unavailable")

    def test_scales_and_rounds(self):
        p = self._Pt(1.0, 2.0, 3.0)
        assert common.ptxyz(p, 10.0) == {"x": 10.0, "y": 20.0, "z": 30.0}

    def test_none_point_is_none(self):
        assert common.ptxyz(None, 10.0) is None

    def test_one_unreadable_component_makes_the_whole_point_null(self):
        # safe(read, 0.0) here publishes {x:10, y:20, z:0} - a real-looking coordinate on the XY
        # plane that nothing measured.
        assert common.ptxyz(self._BlindZ(), 10.0) is None

    def test_a_genuine_zero_coordinate_is_still_an_answer(self):
        # the boundary the null must not swallow: the origin is a position, not a failed read.
        assert common.ptxyz(self._Pt(0.0, 0.0, 0.0), 10.0) == {"x": 0.0, "y": 0.0, "z": 0.0}

    def test_a_non_numeric_component_is_null_not_a_crash(self):
        # an unmodelled adsk property hands back a truthy child object; multiplying it by the unit
        # factor would raise inside the read (or worse, publish whatever it multiplies to).
        assert common.ptxyz(self._Pt(1.0, object(), 3.0), 10.0) is None

    def test_a_boolean_component_is_not_a_coordinate(self):
        # bool is an int subclass: True would otherwise scale to a 10.0 mm coordinate.
        assert common.ptxyz(self._Pt(1.0, 2.0, True), 10.0) is None


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


class TestBodyFacts:
    """The per-body {name, is_solid} projection every feature result is published with. is_solid is
    a published FLAG, so it holds the read_flag contract: True / False / None, never a coerced
    False - a body whose flag will not read is not an open surface."""

    class _Body:
        def __init__(self, name, is_solid):
            self.name, self.isSolid = name, is_solid

    class _BlindBody:
        name = "Mystery"

        @property
        def isSolid(self):
            raise RuntimeError("3 : flag unavailable")

    def test_flags_pass_through(self):
        rows = common.body_facts([self._Body("Solid1", True), self._Body("Srf1", False)])
        assert rows == [{"name": "Solid1", "is_solid": True},
                        {"name": "Srf1", "is_solid": False}]

    def test_an_unreadable_flag_is_null_not_false(self):
        rows = common.body_facts([self._BlindBody()])
        assert rows[0]["is_solid"] is None and rows[0]["name"] == "Mystery"

    def test_no_bodies_is_an_empty_list(self):
        assert common.body_facts([]) == []


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


class _GP:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _AnchorPoint:
    def __init__(self, tag, x=0.0, y=0.0, z=0.0):
        self.tag = tag
        self.geometry = _GP(x, y, z)


class _AnchorLine:
    def __init__(self):
        self.startSketchPoint = _AnchorPoint("start", 0.0, 0.0, 0.0)
        self.endSketchPoint = _AnchorPoint("end", 4.0, 0.0, 0.0)


class _AnchorCircle:
    def __init__(self):
        self.centerSketchPoint = _AnchorPoint("center", 1.0, 1.0, 0.0)


class _AnchorArc:
    def __init__(self):
        self.startSketchPoint = _AnchorPoint("astart")
        self.endSketchPoint = _AnchorPoint("aend")
        self.centerSketchPoint = _AnchorPoint("acenter")


class _MidSketch:
    """Records the SketchPoint and the midpoint constraint the 'mid' anchor creates."""
    def __init__(self):
        self.added = []
        self.midpoints = []
        self.sketchPoints = self
        self.geometricConstraints = self
    def add(self, p):
        self.added.append(p)
        return _AnchorPoint("midpoint")
    def addMidPoint(self, pt, line):
        self.midpoints.append((pt, line))
        return True


class TestParseAnchorRef:
    """The optional THIRD segment of a '<type>:<index>' ref - the ONE grammar sketch_dimension and
    sketch_constrain both read, so an anchor form one verb accepts cannot be rejected by the other."""

    def test_a_bare_ref_carries_no_anchor(self):
        assert common.parse_anchor_ref("line:0") == ("line:0", None, None)

    def test_a_line_endpoint_anchor_splits_off(self):
        assert common.parse_anchor_ref("line:0:end") == ("line:0", "end", None)

    def test_a_circle_centre_anchor_splits_off(self):
        assert common.parse_anchor_ref("circle:2:center") == ("circle:2", "center", None)

    def test_the_anchor_segment_is_case_insensitive(self):
        # entity refs resolve case-insensitively; an anchor that did not would turn a shouted ref
        # into an "unknown anchor" refusal for a form the tools accept in lower case
        assert common.parse_anchor_ref("CIRCLE:0:CENTER") == ("CIRCLE:0", "center", None)

    def test_an_unknown_third_segment_errors_naming_the_valid_anchors(self):
        # silently dropping it would dimension/constrain the WRONG point of the entity
        base, anchor, err = common.parse_anchor_ref("line:0:bogus")
        assert base is None and anchor is None
        assert "unknown anchor" in err and "center" in err


class TestAnchorPoint:
    def test_line_end_and_start(self):
        assert common.anchor_point(None, _AnchorLine(), "end")[0].tag == "end"
        assert common.anchor_point(None, _AnchorLine(), "start")[0].tag == "start"

    def test_circle_centre(self):
        assert common.anchor_point(None, _AnchorCircle(), "center")[0].tag == "center"

    def test_start_on_a_circle_is_refused(self):
        pt, err = common.anchor_point(None, _AnchorCircle(), "start")
        assert pt is None and "line or arc" in err

    def test_centre_on_a_line_is_refused(self):
        pt, err = common.anchor_point(None, _AnchorLine(), "center")
        assert pt is None and "circle or arc" in err

    def test_mid_on_a_line_creates_a_parametrically_welded_point(self, monkeypatch):
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: ("pt", x, y, z))
        sk = _MidSketch()
        pt, err = common.anchor_point(sk, _AnchorLine(), "mid")
        assert err is None and pt.tag == "midpoint"
        assert sk.added == [("pt", 2.0, 0.0, 0.0)]     # the geometric midpoint of 0..4
        assert len(sk.midpoints) == 1                  # welded with a midpoint constraint

    def test_mid_on_an_arc_is_refused(self):
        # an arc has a centre, so 'mid' (a line-only addMidPoint target) is refused, not mis-applied
        pt, err = common.anchor_point(None, _AnchorArc(), "mid")
        assert pt is None and "LINE" in err


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


# ── all_meshes: the ONE design-wide mesh walk, reached through COMPONENTS ────

def _comp_with_meshes(name, mesh_names=(), meshes=None):
    """A component whose meshBodies collection holds the named meshes. `meshes` overrides the
    collection outright (a raiser / a collection yielding None) for the degradation tests."""
    coll = _Coll([type("M", (), {"name": n})() for n in mesh_names]) if meshes is None else meshes
    return type("C", (), {"name": name, "meshBodies": coll})()


class TestAllMeshes:
    def test_walks_every_component_not_just_the_root(self):
        # A mesh imported into a sub-component is design-wide reachable: the walk goes through
        # all_components, so no occurrence needs to exist for the mesh to be found.
        root = _comp_with_meshes("Root")
        sub = _comp_with_meshes("Scan", ["ScanMesh"])
        pairs = common.all_meshes(_design_with(root, [sub]))
        assert [(c.name, m.name) for c, m in pairs] == [("Scan", "ScanMesh")]

    def test_each_mesh_is_paired_with_its_owning_component(self):
        root = _comp_with_meshes("Root", ["A"])
        sub = _comp_with_meshes("Scan", ["B", "C"])
        pairs = common.all_meshes(_design_with(root, [sub]))
        assert [(c.name, m.name) for c, m in pairs] == [
            ("Root", "A"), ("Scan", "B"), ("Scan", "C")]

    def test_a_component_with_an_unreadable_collection_does_not_sink_the_walk(self):
        # A single bad component must not cost every other component's meshes - the survivor check
        # mesh_delete runs on this walk would otherwise report a deleted mesh as still present.
        class _Raiser:
            @property
            def count(self):
                raise RuntimeError("meshBodies unreadable")

        bad = _comp_with_meshes("Broken", meshes=_Raiser())
        good = _comp_with_meshes("Scan", ["ScanMesh"])
        pairs = common.all_meshes(_design_with(bad, [good]))
        assert [m.name for _c, m in pairs] == ["ScanMesh"]

    def test_a_none_item_is_skipped(self):
        root = _comp_with_meshes("Root", meshes=_Coll([None, type("M", (), {"name": "Real"})()]))
        assert [m.name for _c, m in common.all_meshes(_design_with(root, []))] == ["Real"]

    def test_no_meshes_anywhere_is_empty(self):
        assert common.all_meshes(_design_with(_comp_with_meshes("Root"), [])) == []


# ── build_path: the label describes the path BUILT, not the handles passed ───

class _BuiltPath:
    """An adsk.fusion.Path stand-in: `count` is the number of edges the built path holds."""

    def __init__(self, count):
        self.count = count


class _UnreadablePath:
    @property
    def count(self):
        raise RuntimeError("count unreadable")


class TestBuildPathLabel:
    """The 'path' string model_sweep / model_pipe / model_pattern_path publish. What one seed handle
    yields is not predictable from the request - chaining follows tangent continuity, so a seed
    expands to whatever stays tangent (a closed tangent loop chains fully) and stops at a sharp
    corner - so the count must come off the built Path, never off the input."""

    def _stub_handles(self, monkeypatch, n):
        edges = [type("E", (), {})() for _ in range(n)]
        inputs = load_tool("_inputs")
        monkeypatch.setattr(inputs, "GeometryHandleList",
                            lambda *a, **kw: SimpleNamespace(
                                resolve=lambda handles: (edges, None)))
        return edges

    def _comp(self, built):
        return SimpleNamespace(features=SimpleNamespace(
            createPath=lambda seed, is_chain: built))

    def test_a_seed_that_expanded_reports_the_built_count(self, monkeypatch):
        self._stub_handles(monkeypatch, 1)
        _p, label, err = common.build_path(self._comp(_BuiltPath(14)), "EDGE1")
        assert err is None
        assert label == "14 edge(s) from 1 seed handle"

    def test_a_seed_that_did_not_expand_reports_one(self, monkeypatch):
        self._stub_handles(monkeypatch, 1)
        _p, label, err = common.build_path(self._comp(_BuiltPath(1)), "EDGE1")
        assert err is None and label == "1 edge(s) from 1 seed handle"

    def test_unreadable_count_says_so_instead_of_echoing_the_input(self, monkeypatch):
        self._stub_handles(monkeypatch, 1)
        _p, label, err = common.build_path(self._comp(_UnreadablePath()), "EDGE1")
        assert err is None
        assert label == "from 1 seed handle; edge count unreadable"

    def test_several_handles_are_used_exactly(self, monkeypatch):
        import adsk.fusion
        self._stub_handles(monkeypatch, 3)
        monkeypatch.setattr(adsk.fusion.Path, "create",
                            staticmethod(lambda coll, opts: _BuiltPath(3)))
        _p, label, err = common.build_path(self._comp(None), ["E1", "E2", "E3"])
        assert err is None and label == "3 edge(s) from 3 handles, used exactly"

    def test_the_map_blurb_states_the_tangent_continuity_rule(self):
        # the helper map is what an author reads before wiring build_path: it must promise neither
        # unconditional chaining nor its opposite (a tangent-continuous CLOSED loop chained all 8
        # edges from one seed) - only tangent continuity, and the built count as the answer.
        blurb = common.MAP_BLURB
        assert "TANGENT connections" in blurb
        assert "sharp corner stops the chain" in blurb
        assert "count is the truth" in blurb
        assert "auto-chain" not in blurb.lower()
        assert "closed loop" not in blurb.lower() and "seed edge alone" not in blurb


class TestApplyRename:
    """apply_rename - the ONE create-flow rename-with-disclosure: a declined or deduped rename is
    returned as a warning beside the ACTUAL name, never swallowed and never an error."""

    def test_a_clean_rename_returns_the_new_name_and_no_warning(self):
        ent = SimpleNamespace(name="Sketch1")
        final, warning = common.apply_rename(ent, "Pocket Outline")
        assert final == "Pocket Outline" and warning is None
        assert ent.name == "Pocket Outline"

    def test_an_empty_request_renames_nothing_and_warns_nothing(self):
        ent = SimpleNamespace(name="Joint1")
        for req in ("", "   ", None):
            final, warning = common.apply_rename(ent, req)
            assert final == "Joint1" and warning is None

    def test_a_raising_rename_is_disclosed_with_the_kept_name(self):
        class Stubborn:
            @property
            def name(self):
                return "Joint1"

            @name.setter
            def name(self, v):
                raise RuntimeError("3 : name is read-only here")

        final, warning = common.apply_rename(Stubborn(), "Hinge")
        assert final == "Joint1"
        assert "Hinge" in warning and "Joint1" in warning and "failed" in warning

    def test_a_silently_swallowed_rename_is_disclosed_not_reported_as_taken(self):
        # the FR-13 shape: entity.name = x raises nothing and changes nothing - only the
        # read-back catches it, and the warning names both the request and what the entity holds
        class Swallowing:
            @property
            def name(self):
                return "Sketch1"

            @name.setter
            def name(self, v):
                pass

        final, warning = common.apply_rename(Swallowing(), "Outline")
        assert final == "Sketch1"
        assert "Outline" in warning and "did not take" in warning

    def test_a_deduped_landing_reports_the_variant_that_landed(self):
        # the platform dedupes a colliding name ('Foo' -> 'Foo(1)'): the payload's name is the
        # read-back, and the warning says the requested name is not what it holds
        class Deduping:
            def __init__(self):
                self._n = "Body1"

            @property
            def name(self):
                return self._n

            @name.setter
            def name(self, v):
                self._n = v + "(1)"

        final, warning = common.apply_rename(Deduping(), "Bracket")
        assert final == "Bracket(1)"
        assert "Bracket" in warning and "Bracket(1)" in warning


# ── timeline_health: the delta is keyed on identity, not on the repeated name ────────────────────

def _row(name, health, token=None):
    """One timeline row: the three properties timeline_health reads. A row with no token models a
    TimelineGroup / a feature class with no public-API entity, both of which answer entity as None.
    """
    entity = SimpleNamespace(entityToken=token) if token is not None else None
    return SimpleNamespace(name=name, healthState=health, entity=entity)


def _timeline(*rows):
    return type("D", (), {"timeline": _Coll(rows)})()


class TestTimelineHealth:
    """timeline_health hands back NAMES, but timeline names repeat across components (two 'Sketch1',
    two 'Extrude1' in a two-component design), so the delta the guards compute over those lists is
    keyed on the row's entity token instead."""

    def test_health_states_split_into_errors_and_warnings(self):
        errors, warnings, total = common.timeline_health(_timeline(
            _row("Sketch1", 0, "T:a"), _row("Extrude1", 2, "T:b"), _row("Fillet1", 1, "T:c")))
        assert list(errors) == ["Extrude1"] and list(warnings) == ["Fillet1"] and total == 3

    def test_a_design_with_no_timeline_reports_nothing(self):
        assert common.timeline_health(type("D", (), {"timeline": None})()) == ([], [], 0)

    def test_limit_bounds_the_walk_to_the_first_rows(self):
        tl = _timeline(_row("Extrude1", 2, "T:a"), _row("Constraint1", 2, "T:b"))
        errors, _w, total = common.timeline_health(tl, limit=1)
        assert list(errors) == ["Extrude1"] and total == 1

    def test_damage_to_a_twin_is_seen_past_an_already_broken_namesake(self):
        # two components each hold an 'Extrude1'; one is already broken, then the OTHER breaks.
        # Keyed on the name, the delta sees a name already present and reports nothing.
        before, _w, _t = common.timeline_health(_timeline(
            _row("Extrude1", 2, "T:a"), _row("Extrude1", 0, "T:b")))
        after, _w, _t = common.timeline_health(_timeline(
            _row("Extrude1", 2, "T:a"), _row("Extrude1", 2, "T:b")))
        assert [n for n in after if n not in before] == ["Extrude1"]
        assert sorted(set(after) - set(before)) == ["Extrude1"]

    def test_a_feature_broken_before_the_edit_is_not_reported_as_new_damage(self):
        # the same row read twice reports the same token, so the guard stays quiet
        rows = (_row("Extrude1", 2, "T:a"), _row("Extrude1", 0, "T:b"))
        before, _w, _t = common.timeline_health(_timeline(*rows))
        after, _w, _t = common.timeline_health(_timeline(*rows))
        assert [n for n in after if n not in before] == []
        assert set(after) - set(before) == set()

    def test_one_twin_healing_while_the_other_breaks_names_the_broken_one(self):
        # the reverse miss: the name is in both lists yet a DIFFERENT feature carries it
        before, _w, _t = common.timeline_health(_timeline(
            _row("Sketch1", 2, "T:a"), _row("Sketch1", 0, "T:b")))
        after, _w, _t = common.timeline_health(_timeline(
            _row("Sketch1", 0, "T:a"), _row("Sketch1", 2, "T:b")))
        assert [n for n in after if n not in before] == ["Sketch1"]

    def test_rows_with_no_readable_token_key_on_their_name(self):
        before, _w, _t = common.timeline_health(_timeline(_row("Group1", 2)))
        after, _w, _t = common.timeline_health(_timeline(_row("Group1", 2), _row("Group2", 2)))
        assert [n for n in after if n not in before] == ["Group2"]

    def test_a_tokenless_row_compares_equal_to_the_plain_name_it_reports(self):
        errors, _w, _t = common.timeline_health(_timeline(_row("Extrude1", 2)))
        assert errors == ["Extrude1"] and set(errors) == {"Extrude1"}

    def test_an_unreadable_name_falls_back_to_the_row_index(self):
        class Nameless:
            healthState = 2
            entity = None

            @property
            def name(self):
                raise RuntimeError("3 : unreadable")

        errors, _w, _t = common.timeline_health(_timeline(Nameless()))
        assert list(errors) == ["#0"]

    def test_the_names_still_cross_the_wire_as_plain_strings(self):
        errors, warnings, _t = common.timeline_health(_timeline(
            _row("Extrude1", 2, "T:a"), _row("Fillet1", 1, "T:b")))
        payload = json.loads(common.ok({"e": errors, "w": warnings})["content"][0]["text"])
        assert payload == {"e": ["Extrude1"], "w": ["Fillet1"]}
        assert ", ".join(errors) == "Extrude1"
