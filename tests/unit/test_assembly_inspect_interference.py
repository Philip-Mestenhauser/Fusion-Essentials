"""Unit tests for assembly_inspect_interference - the physical-fit 'check my work' tool.

The live analyzeInterference call needs Fusion, but the logic worth pinning is pure: planning which
placed-body pairs to run (box-pruned, capped), aggregating overlap volume per pair, the clear=true
path, and the body-less-occurrence census refusal.
"""

from types import SimpleNamespace

import pytest

from conftest import (BRepBody, FakeBoundingBox3D, FakePoint, _NamedCollection, install, load_tool,
                      make_occurrence, MakeComp, MakeDesign, payload, body_proxy)

ai = load_tool("assembly_inspect_interference")


def _boxed(minp, maxp):
    return FakeBoundingBox3D(FakePoint(*minp), FakePoint(*maxp))


def _solid(name, minp, maxp, is_solid=True):
    return BRepBody(name=name, bbox=_boxed(minp, maxp), is_solid=is_solid)


def _occ(path, bodies=(), children=(), broken_children=()):
    """An occurrence in the analysis set: bRepBodies are the bodies THIS occurrence places (what
    occ.bRepBodies reads live, and what the per-pair analysis feeds createInterferenceInput); its
    component's own `occurrences` is what the recursed walk falls back to for nested/broken
    children."""
    comp = MakeComp(name=path.split(":")[0], occurrences=list(broken_children) + list(children))
    return make_occurrence(path=path, component=comp, bodies=list(bodies), children=children)


UNAVAILABLE = ("3 : The occurrence's referenced component is unavailable (broken or missing "
               "external reference).")

# The walk that will not enumerate at all: reading root.allOccurrences on a design holding an
# unresolved reference RAISES, and an empty analysis set yields zero interferences.
RAISING_WALK = "2 : InternalValidationError : occ"


def _broken_occ(name="45740"):
    """An occurrence whose referenced component will not load - it carries NO geometry this analysis
    could compare, and its path will not read either."""
    return make_occurrence(path=name, raises_on={
        "component": UNAVAILABLE,
        "fullPathName": "2 : InternalValidationError : path.valid()"})


class FakeInterfBody:
    def __init__(self, volume):
        self.volume = volume


class FakeResult:
    def __init__(self, b1, b2, volume):
        self.entityOne = b1.nativeObject or b1
        self.entityTwo = b2.nativeObject or b2
        self.interferenceBody = FakeInterfBody(volume)


class FakeResults:
    def __init__(self, results):
        self._r = list(results)
    @property
    def count(self):
        return len(self._r)
    def item(self, i):
        return self._r[i]


class FakeInput:
    areCoincidentFacesIncluded = False


class FakeInputRejectsCoincident:
    """Raises when areCoincidentFacesIncluded is set - models the API rejecting the value. The set
    must not be swallowed by a try/except: a rejected value surfaces as an analysis failure."""
    def __setattr__(self, name, value):
        if name == "areCoincidentFacesIncluded":
            raise RuntimeError("areCoincidentFacesIncluded rejected by the API")
        object.__setattr__(self, name, value)


class _PairInterferenceDesign(MakeDesign):
    """A design whose analyzeInterference answers every REGISTERED body pair present in the SAME
    call's own createInterferenceInput collection - a call whose collection holds several bodies can
    answer several results at once, the shape a self-overlap riding along a cross pair needs.
    `pair_volumes` maps frozenset({id(body), id(body)}) -> a volume, or a list of volumes for
    several interference bodies between one pair; an unregistered pair answers zero results,
    matching a real non-interfering pair. `calls` records each call's own body list, so a
    pruned/capped pair can be proven to have never reached the API."""

    def __init__(self, comp, pair_volumes=None, reject_coincident=False):
        MakeDesign.__init__(self, comp=comp)
        self._pair_volumes = pair_volumes or {}
        self._reject_coincident = reject_coincident
        self.calls = []

    def createInterferenceInput(self, occs):
        self.calls.append(list(occs))
        return FakeInputRejectsCoincident() if self._reject_coincident else FakeInput()

    def analyzeInterference(self, inp):
        # Every overlapping body pair in the input can contribute a result.
        bodies = self.calls[-1]
        body_ids = {id(b) for b in bodies}
        by_id = {id(b): b for b in bodies}
        out = []
        for pair_key, entry in self._pair_volumes.items():
            if not pair_key <= body_ids:
                continue
            b1, b2 = (by_id[i] for i in pair_key)
            vols = entry if isinstance(entry, (list, tuple)) else [entry]
            out.extend(FakeResult(b1, b2, v) for v in vols)
        return FakeResults(out)


@pytest.fixture
def world():
    """A design whose analysis set is `occurrences`, wired into the tool through install() - which
    patches both design seams and the ObjectCollection the per-pair bodies are collected into.
    Returns the design itself, so a test can read back `.calls` (pruning/cap proof)."""
    def _build(occurrences, pair_volumes=None, reject_coincident=False, root_bodies=()):
        comp = MakeComp(name="Root", occurrences=list(occurrences), bodies=list(root_bodies))
        des = _PairInterferenceDesign(comp, pair_volumes=pair_volumes,
                                      reject_coincident=reject_coincident)
        return install(ai, des)
    return _build


class TestTouchBoundary:
    """_touch is the prune gate: keep a pair unless its boxes PROVABLY cannot meet."""

    def test_boxes_sharing_exactly_one_boundary_plane_are_kept_not_pruned(self):
        a = ai._entity_box([_solid("A", (0, 0, 0), (5, 5, 5))])
        b = ai._entity_box([_solid("B", (5, 0, 0), (10, 5, 5))])
        assert ai._touch(a, b) is True

    def test_a_hairline_gap_beyond_the_boundary_is_pruned(self):
        a = ai._entity_box([_solid("A", (0, 0, 0), (5, 5, 5))])
        b = ai._entity_box([_solid("B", (5.0001, 0, 0), (10, 5, 5))])
        assert ai._touch(a, b) is False

    def test_an_unreadable_box_on_either_side_is_never_pruned(self):
        readable = ai._entity_box([_solid("A", (0, 0, 0), (5, 5, 5))])
        assert ai._touch(None, readable) is True
        assert ai._touch(readable, None) is True


class TestMultiInstanceNaming:
    @pytest.mark.parametrize("body_count", [1, 2])
    def test_overlapping_placements_of_the_same_bodies_are_not_deduplicated(self, world, body_count):
        one, two = _occ("Part:1"), _occ("Part:2")
        natives = [_solid(f"Body{i}", (i * 5, 0, 0), (i * 5 + 2, 2, 2))
                   for i in range(body_count)]
        first = [body_proxy(b, one) for b in natives]
        second = [body_proxy(b, two) for b in natives]
        for i, placed in enumerate(second):
            object.__setattr__(placed, "boundingBox", _boxed((i * 5 + 1, 0, 0), (i * 5 + 3, 2, 2)))
        one.bRepBodies, two.bRepBodies = _NamedCollection(first), _NamedCollection(second)
        world([one, two], pair_volumes={
            frozenset({id(a), id(b)}): 4.0 for a, b in zip(first, second)})
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["interferences"] == [
            {"occurrence_one": "Part:1", "occurrence_two": "Part:2",
             "overlap_volume_cm3": 4.0 * body_count}]

    def test_shared_labels_do_not_merge_distinct_placement_pairs(self, world):
        a, b, c = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "ABC"]
        world([_occ("Same:1", bodies=[a]), _occ("Same:1", bodies=[b]),
               _occ("Other:1", bodies=[c])], pair_volumes={
                   frozenset({id(a), id(c)}): 1.0,
                   frozenset({id(b), id(c)}): 2.0})
        out = payload(ai.handler())
        assert out["measured"]["interference_count"] == 2
        assert [r["overlap_volume_cm3"] for r in out["measured"]["interferences"]] == [2.0, 1.0]

    def test_two_instances_of_one_component_name_their_own_block_and_volume(self, world):
        # ONE component (Peg) placed twice, each instance overlapping a DIFFERENT block - the exact
        # instance now comes straight off the per-pair analysis, never guessed from candidates.
        peg1, peg2 = _occ("Peg:1"), _occ("Peg:2")
        native = _solid("PegBody", (0, 0, 0), (2, 2, 2))
        peg1_body, peg2_body = body_proxy(native, peg1), body_proxy(native, peg2)
        object.__setattr__(peg2_body, "boundingBox", _boxed((48, 0, 0), (50, 2, 2)))
        peg1.bRepBodies, peg2.bRepBodies = _NamedCollection([peg1_body]), _NamedCollection([peg2_body])
        block_a = _solid("BlockABody", (0, 0, 0), (10, 10, 10))
        block_b = _solid("BlockBBody", (48, 0, 0), (58, 10, 10))
        blk_a, blk_b = _occ("BlockA:1", bodies=[block_a]), _occ("BlockB:1", bodies=[block_b])
        world([peg1, blk_a, peg2, blk_b], pair_volumes={
            frozenset({id(peg1_body), id(block_a)}): 1.0,
            frozenset({id(peg2_body), id(block_b)}): 0.36,
        })
        out = payload(ai.handler())
        rows = {tuple(sorted([r["occurrence_one"], r["occurrence_two"]])): r
                for r in out["measured"]["interferences"]}
        a = rows[tuple(sorted(["Peg:1", "BlockA:1"]))]
        b = rows[tuple(sorted(["Peg:2", "BlockB:1"]))]
        assert a["overlap_volume_cm3"] == 1.0 and b["overlap_volume_cm3"] == 0.36
        assert "occurrence_one_candidates" not in a and "occurrence_two_candidates" not in a
        assert "occurrence_one_candidates" not in b and "occurrence_two_candidates" not in b
        assert out["measured"]["interference_count"] == 2
        assert ai.RETURNS[0].assert_present(out) == ""


class TestSelfOverlapAttribution:
    """Internal body overlaps belong only to their occurrence's self-pair."""

    def _bodies(self):
        # B touches A2's box but has no registered overlap by default.
        a1 = _solid("A1", (0, 0, 0), (5, 5, 5))
        a2 = _solid("A2", (3, 0, 0), (8, 5, 5))          # overlaps a1
        b1 = _solid("B1", (6, 0, 0), (11, 5, 5))         # touches A2
        return a1, a2, b1

    def _rig(self, world, a1, a2, b1, extra_pairs=None):
        pair_volumes = {frozenset({id(a1), id(a2)}): 4.0}
        pair_volumes.update(extra_pairs or {})
        world([_occ("A:1", bodies=[a1, a2]), _occ("B:1", bodies=[b1])],
             pair_volumes=pair_volumes)

    def test_one_multibody_occurrence_can_be_checked_for_self_overlap(self, world):
        a1, a2, _ = self._bodies()
        world([_occ("Solo:1", bodies=[a1, a2])],
              pair_volumes={frozenset({id(a1), id(a2)}): 4.0})
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["pairs_analyzed"] == 1
        assert out["measured"]["interferences"] == [
            {"occurrence_one": "Solo:1", "occurrence_two": "Solo:1", "overlap_volume_cm3": 4.0}]

    def test_a_cross_pair_reports_no_interference_when_only_A_overlaps_itself(self, world):
        self._rig(world, *self._bodies())
        out = payload(ai.handler())
        rows = {tuple(sorted([r["occurrence_one"], r["occurrence_two"]])): r
                for r in out["measured"]["interferences"]}
        assert tuple(sorted(["A:1", "B:1"])) not in rows           # no cross-pair interference
        self_row = rows[("A:1", "A:1")]                            # the internal overlap still lands
        assert self_row["overlap_volume_cm3"] == 4.0
        assert out["measured"]["interference_count"] == 1

    def test_a_cross_pair_reports_only_the_real_shared_volume(self, world):
        a1, a2, b1 = self._bodies()
        self._rig(world, a1, a2, b1, extra_pairs={frozenset({id(a2), id(b1)}): 1.5})
        out = payload(ai.handler())
        rows = {tuple(sorted([r["occurrence_one"], r["occurrence_two"]])): r
                for r in out["measured"]["interferences"]}
        cross = rows[tuple(sorted(["A:1", "B:1"]))]
        assert cross["overlap_volume_cm3"] == 1.5                  # NOT 4.0 + 1.5
        assert rows[("A:1", "A:1")]["overlap_volume_cm3"] == 4.0   # the self overlap is unchanged
        assert out["measured"]["interference_count"] == 2

class TestPruning:
    def test_a_pair_whose_boxes_cannot_touch_is_pruned_not_analysed(self, world):
        near_a = _solid("A", (0, 0, 0), (1, 1, 1))
        near_b = _solid("B", (0.5, 0, 0), (1.5, 1, 1))       # touches A
        far = _solid("C", (1000, 0, 0), (1001, 1, 1))        # nowhere near either
        occs = [_occ("A:1", bodies=[near_a]), _occ("B:1", bodies=[near_b]),
                _occ("C:1", bodies=[far])]
        des = world(occs, pair_volumes={frozenset({id(near_a), id(near_b)}): 0.0})
        out = payload(ai.handler())
        assert out["measured"]["pairs_pruned"] == 2            # A-C and B-C
        assert out["measured"]["pairs_analyzed"] == 1           # only A-B
        assert len(des.calls) == 1
        assert {id(b) for b in des.calls[0]} == {id(near_a), id(near_b)}


class TestPairCap:
    def test_multiple_bodies_do_not_bypass_the_native_call_cap(self, monkeypatch, world):
        monkeypatch.setattr(ai, "_PAIR_CAP", 3)
        bodies = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "ABCD"]
        des = world([_occ("A:1", bodies=bodies[:2]), _occ("B:1", bodies=bodies[2:])])
        res = ai.handler()
        assert res["isError"] is True and "3 placed-body pair analysis cap" in res["message"]
        assert len(des.calls) == 3 and all(len(call) == 2 for call in des.calls)
        assert "Interference command in Fusion" in res["message"]
        assert "model_measure_relation" not in res["message"]
        assert "Resolve the reference" not in res["message"]

    def test_the_cap_stops_analysis_and_names_what_was_skipped(self, monkeypatch, world):
        monkeypatch.setattr(ai, "_PAIR_CAP", 1)
        # three mutually-overlapping occurrences -> C(3,2)=3 touching pairs, over the cap of 1.
        a, b, c = (_solid(n, (0, 0, 0), (5, 5, 5)) for n in "ABC")
        occs = [_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b]), _occ("C:1", bodies=[c])]
        des = world(occs, pair_volumes={})
        res = ai.handler()
        assert res["isError"] is True
        assert "1 placed-body pair analysis cap" in res["message"]
        assert "not analysed" in res["message"]
        assert len(des.calls) == 1                              # the cap actually stopped the loop

    def test_a_found_interference_still_stands_when_the_cap_left_pairs_unanalysed(
            self, monkeypatch, world):
        monkeypatch.setattr(ai, "_PAIR_CAP", 1)
        a, b, c = (_solid(n, (0, 0, 0), (5, 5, 5)) for n in "ABC")
        occs = [_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b]), _occ("C:1", bodies=[c])]
        world(occs, pair_volumes={frozenset({id(a), id(b)}): 2.0})
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["interference_count"] == 1
        assert "cap" in out["note"].lower()

    def test_a_volume_cut_off_mid_occurrence_pair_is_partial(self, monkeypatch, world):
        monkeypatch.setattr(ai, "_PAIR_CAP", 1)
        a, b, c = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "ABC"]
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b, c])],
              pair_volumes={frozenset({id(a), id(b)}): 2.0})
        out = payload(ai.handler())
        assert out["measured"]["interferences"][0]["partial"] is True
        assert out["measured"]["analysis_complete"] is False
        assert out["measured"]["pairs_omitted"] == 2
        assert "total overlap volume" not in out["note"]

    def test_time_budget_stops_between_calls_and_marks_partial_volume(self, monkeypatch, world):
        monkeypatch.setattr(ai, "_TIME_BUDGET_S", 20.0)
        monkeypatch.setattr(ai, "_PAIR_CAP", 2)
        ticks = iter([0.0, 0.0, 21.0])
        monkeypatch.setattr(ai, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        a, b, c = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "ABC"]
        des = world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b, c])],
                    pair_volumes={frozenset({id(a), id(b)}): 2.0})
        out = payload(ai.handler())
        measured = out["measured"]
        assert len(des.calls) == 1
        assert measured["pairs_analyzed"] == 1 and measured["pairs_omitted"] == 2
        assert measured["analysis_complete"] is False
        assert measured["interferences"][0]["partial"] is True
        assert "20 s analysis budget" in out["note"]
        assert "analysis cap" not in out["note"]

    def test_time_budget_refuses_a_clean_verdict(self, monkeypatch, world):
        ticks = iter([0.0, 21.0])
        monkeypatch.setattr(ai, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        a, b = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "AB"]
        des = world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])])
        res = ai.handler()
        assert res["isError"] is True
        assert "20 s analysis budget" in res["message"]
        assert "0 placed-body pair(s) WERE analysed" in res["message"]
        assert des.calls == []

    def test_pairs_exactly_at_the_cap_are_all_analysed_not_capped(self, monkeypatch, world):
        # the boundary: total planned == cap must run every pair, not read as "over" it.
        monkeypatch.setattr(ai, "_PAIR_CAP", 2)
        a = _solid("A", (0, 0, 0), (5, 5, 5))
        b = _solid("B", (4, 0, 0), (9, 5, 5))       # touches A
        c = _solid("C", (8, 0, 0), (13, 5, 5))      # touches B, not A
        occs = [_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b]), _occ("C:1", bodies=[c])]
        des = world(occs, pair_volumes={})
        out = payload(ai.handler())
        assert out["measured"]["pairs_analyzed"] == 2
        assert len(des.calls) == 2
        assert "cap" not in out["note"].lower()


class TestBodylessCensus:
    def test_two_body_less_occurrences_refuse_rather_than_pass(self, world):
        # comparable count is ZERO, not two - the false pass this fix exists to close.
        world([_occ("EmptyA:1", bodies=[]), _occ("EmptyB:1", bodies=[])], pair_volumes={})
        res = ai.handler()
        assert res["isError"] is True
        assert "EmptyA:1" in res["message"] and "EmptyB:1" in res["message"]
        assert "NOT a pass" in res["message"]

    def test_one_body_less_occurrence_among_solids_is_excluded_not_refused(self, world):
        # a wrapper-style occurrence with no bodies of its own must not block a real comparison
        # between the two occurrences that DO own bodies.
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        occs = [_occ("Wrapper:1", bodies=[]), _occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])]
        world(occs, pair_volumes={frozenset({id(a), id(b)}): 3.0})
        out = payload(ai.handler())
        assert out["measured"]["occurrences_checked"] == 2
        assert out["measured"]["interference_count"] == 1


class TestInterferenceHandler:
    def test_reports_pairs_by_occurrence_with_volume(self, world):
        wheel, fork = _solid("W", (0, 0, 0), (5, 5, 5)), _solid("F", (0, 0, 0), (5, 5, 5))
        world([_occ("Wheel:1", bodies=[wheel]), _occ("Fork:1", bodies=[fork])],
             pair_volumes={frozenset({id(wheel), id(fork)}): 7.7})
        out = payload(ai.handler())
        assert out["passed"] is False and out["measured"]["interference_count"] == 1
        assert out["relation"] == "interference_free"
        pair = out["measured"]["interferences"][0]
        assert {pair["occurrence_one"], pair["occurrence_two"]} == {"Wheel:1", "Fork:1"}
        assert pair["overlap_volume_cm3"] == 7.7

    def test_aggregates_multiple_interference_bodies_between_one_pair(self, world):
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        world([_occ("Crank:1", bodies=[a]), _occ("Wheel:1", bodies=[b])],
             pair_volumes={frozenset({id(a), id(b)}): [3.0, 2.0]})
        out = payload(ai.handler())
        assert out["measured"]["interference_count"] == 1
        assert out["measured"]["interferences"][0]["overlap_volume_cm3"] == 5.0

    def test_zero_volume_result_is_a_coincident_contact(self, world):
        a, b = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "AB"]
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])],
              pair_volumes={frozenset({id(a), id(b)}): 0.0})
        out = payload(ai.handler(include_coincident_faces=True))
        assert out["passed"] is False
        assert out["measured"]["interferences"] == [
            {"occurrence_one": "A:1", "occurrence_two": "B:1", "overlap_volume_cm3": 0.0}]
        assert "coincident" in out["note"]

    @pytest.mark.parametrize("bad_volume", [None, float("nan"), float("inf"), -1.0])
    def test_unreadable_volume_keeps_the_pair_without_inventing_zero(self, world, bad_volume):
        a, b = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "AB"]
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])],
              pair_volumes={frozenset({id(a), id(b)}): [2.0, bad_volume]})
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["interferences"][0]["overlap_volume_cm3"] is None

    def test_unreadable_result_count_refuses(self, monkeypatch, world):
        a, b = [_solid(n, (0, 0, 0), (2, 2, 2)) for n in "AB"]
        des = world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])])
        monkeypatch.setattr(des, "analyzeInterference", lambda _inp: SimpleNamespace(count=None))
        res = ai.handler()
        assert res["isError"] is True
        assert "result count did not read" in res["message"]

    def test_clear_when_nothing_registers_a_volume(self, world):
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])], pair_volumes={})
        out = payload(ai.handler())
        assert out["passed"] is True and out["measured"]["interference_count"] == 0

    def test_under_two_comparable_entities_REFUSES_rather_than_passing(self, world):
        a = _solid("A", (0, 0, 0), (1, 1, 1))
        world([_occ("Solo:1", bodies=[a])], pair_volumes={})
        res = ai.handler()
        assert res["isError"] is True
        assert "NOT a pass" in res["message"]

    def test_nested_parts_under_ONE_top_level_occurrence_are_still_analysed(self, world):
        # a wrapper occurrence owning no body of its own excludes it from the count, but its
        # (flat-walked) children are still compared.
        a, b = _solid("BoxA", (0, 0, 0), (5, 5, 5)), _solid("BoxB", (0, 0, 0), (5, 5, 5))
        occs = [_occ("Wrapper:1", bodies=[]),
                _occ("Wrapper:1+PartA:1", bodies=[a]),
                _occ("Wrapper:1+PartB:1", bodies=[b])]
        world(occs, pair_volumes={frozenset({id(a), id(b)}): 500.0})
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["interferences"] == [
            {"occurrence_one": "Wrapper:1+PartA:1", "occurrence_two": "Wrapper:1+PartB:1",
             "overlap_volume_cm3": 500.0}]

    def test_pairs_sorted_by_descending_volume(self, world):
        bodies = {n: _solid(n, (0, 0, 0), (5, 5, 5)) for n in "ABCD"}
        occs = [_occ(f"{n}:1", bodies=[bodies[n]]) for n in "ABCD"]
        world(occs, pair_volumes={
            frozenset({id(bodies["A"]), id(bodies["B"])}): 1.0,
            frozenset({id(bodies["C"]), id(bodies["D"])}): 9.0,
            frozenset({id(bodies["A"]), id(bodies["C"])}): 4.0,
        })
        out = payload(ai.handler())
        vols = [p["overlap_volume_cm3"] for p in out["measured"]["interferences"]]
        assert vols == [9.0, 4.0, 1.0]
        assert out["measured"]["interference_count"] == 3

    def test_coincident_flag_echoed(self, world):
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])], pair_volumes={})
        default = payload(ai.handler())
        assert default["tolerance_used"]["coincident_faces_included"] is False
        incl = payload(ai.handler(include_coincident_faces=True))
        assert incl["tolerance_used"]["coincident_faces_included"] is True

    def test_occurrences_checked_count(self, world):
        bodies = [_solid(n, (i * 100, 0, 0), (i * 100 + 1, 1, 1)) for i, n in enumerate("ABC")]
        occs = [_occ(f"{n}:1", bodies=[b]) for n, b in zip("ABC", bodies)]
        world(occs, pair_volumes={})
        assert payload(ai.handler())["measured"]["occurrences_checked"] == 3

    def test_self_pair_when_one_occurrence_owns_two_overlapping_bodies(self, world):
        # two bodies OF THE SAME occurrence overlapping each other - a self-pair.
        a, b = _solid("BodyX", (0, 0, 0), (5, 5, 5)), _solid("BodyY", (0, 0, 0), (5, 5, 5))
        wheel = _occ("Wheel:1", bodies=[a, b])
        far = _occ("Other:1", bodies=[_solid("Far", (1000, 0, 0), (1001, 1, 1))])
        world([wheel, far], pair_volumes={frozenset({id(a), id(b)}): 2.0})
        out = payload(ai.handler())
        pair = out["measured"]["interferences"][0]
        assert pair["occurrence_one"] == "Wheel:1" and pair["occurrence_two"] == "Wheel:1"
        assert out["measured"]["interference_count"] == 1

    def test_no_design_errors(self, monkeypatch):
        monkeypatch.setattr(ai._common, "design", lambda: None)
        res = ai.handler()
        assert res["isError"] is True
        assert "No active design" in res["message"]

    def test_areCoincidentFacesIncluded_failure_surfaces_as_error(self, world):
        # a rejected areCoincidentFacesIncluded assignment must raise into the handler's error path,
        # not be swallowed into a successful result.
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])], pair_volumes={},
             reject_coincident=True)
        res = ai.handler(include_coincident_faces=True)
        assert res["isError"] is True
        assert "areCoincidentFacesIncluded rejected" in res["message"]


class TestUnresolvedReferences:
    """The cardinal sin this tool was one raise away from: root.allOccurrences raising left the
    analysis set EMPTY, and an empty set produces zero interferences - published as passed=true."""

    def test_a_raising_walk_no_longer_analyses_an_empty_set(self, world):
        a, b = _solid("A", (0, 0, 0), (1, 1, 1)), _solid("B", (100, 0, 0), (101, 1, 1))
        des = world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])], pair_volumes={})
        des.rootComponent.allOccurrences = _NamedCollection(raises=RAISING_WALK)
        out = payload(ai.handler())
        assert out["measured"]["occurrences_checked"] == 2      # NOT 0, and NOT a refusal
        assert out["measured"]["occurrences_walk"] == "recursed"
        assert out["passed"] is True

    def test_a_clean_verdict_is_REFUSED_while_an_unresolved_reference_is_excluded(self, world):
        # a pass is a claim about everything, and an unresolved occurrence was never in the set.
        a, b = _solid("A", (0, 0, 0), (1, 1, 1)), _solid("B", (100, 0, 0), (101, 1, 1))
        occs = [_occ("A:1", bodies=[a]),
                _occ("B:1", bodies=[b], broken_children=[_broken_occ("45740")])]
        des = world(occs, pair_volumes={})
        des.rootComponent.allOccurrences = _NamedCollection(raises=RAISING_WALK)
        res = ai.handler()
        assert res["isError"] is True
        assert "Cannot certify interference-free" in res["message"]
        assert "45740" in res["message"]
        assert "no pass was formed" in res["message"]

    def test_a_POSITIVE_finding_still_stands_over_an_incomplete_set(self, world):
        # finding one overlapping pair is proof on its own - it does not depend on completeness.
        a, b = _solid("A", (0, 0, 0), (5, 5, 5)), _solid("B", (0, 0, 0), (5, 5, 5))
        occs = [_occ("A:1", bodies=[a]),
                _occ("B:1", bodies=[b], broken_children=[_broken_occ("45740")])]
        des = world(occs, pair_volumes={frozenset({id(a), id(b)}): 2.0})
        des.rootComponent.allOccurrences = _NamedCollection(raises=RAISING_WALK)
        out = payload(ai.handler())
        assert out["passed"] is False
        assert out["measured"]["unresolved_references"] == ["45740"]
        assert "were NOT compared" in out["note"]

    def test_zero_unresolved_leaves_the_pass_verdict_and_the_fast_walk(self, world):
        a, b = _solid("A", (0, 0, 0), (1, 1, 1)), _solid("B", (100, 0, 0), (101, 1, 1))
        world([_occ("A:1", bodies=[a]), _occ("B:1", bodies=[b])], pair_volumes={})
        out = payload(ai.handler())
        assert out["passed"] is True
        assert out["measured"]["occurrences_walk"] == "allOccurrences"
        assert out["measured"]["unresolved_references"] == []
        assert "were NOT compared" not in out["note"]
