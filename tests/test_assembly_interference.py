"""Unit tests for assembly_interference — the physical-fit 'check my work' tool.

The live analyzeInterference call needs Fusion, but the logic worth pinning is pure: mapping an
interfering body back to its OWNING occurrence (so the report is by part, not 'Body1'), aggregating
overlap volume per occurrence-pair, the clear=true path, and the <2-occurrence short-circuit.
"""

import json

from conftest import load_tool

ai = load_tool("assembly_interference")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeOcc:
    def __init__(self, name):
        self.name = name


class FakeBody:
    # LIVE shape: interference bodies expose the owner via parentComponent (assemblyContext is None).
    def __init__(self, name, comp_name=None, occ_name=None):
        self.name = name
        self.parentComponent = FakeOcc(comp_name) if comp_name else None
        self.assemblyContext = FakeOcc(occ_name) if occ_name else None


class FakeInterfBody:
    def __init__(self, volume):
        self.volume = volume


class FakeResult:
    def __init__(self, b1, b2, volume):
        self.entityOne = b1
        self.entityTwo = b2
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


class FakeOccColl:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class FakeRoot:
    def __init__(self, occurrences):
        self.occurrences = occurrences


class FakeDesign:
    def __init__(self, occurrences, results):
        self.rootComponent = FakeRoot(occurrences)
        self._results = results
    def createInterferenceInput(self, occs):
        return FakeInput()
    def analyzeInterference(self, inp):
        return FakeResults(self._results)


def _install(occurrences, results):
    des = FakeDesign(occurrences, results)
    # the handler resolves the design via _common.design(); patch that (the seam _inputs uses) rather
    # than the module's app — so the tool can share _common.design instead of a local _design() copy.
    ai._common.design = lambda: des
    import adsk.core
    adsk.core.ObjectCollection.create = staticmethod(FakeOccColl)
    return des


class TestOwningOccurrence:
    def test_prefers_parent_component_name(self):
        # the live-validated path: parentComponent.name (assemblyContext is None on these bodies)
        b = FakeBody("Body1", comp_name="Wheel")
        assert ai._owning_occurrence_name(b) == "Wheel"

    def test_falls_back_to_assembly_context_then_body_name(self):
        assert ai._owning_occurrence_name(FakeBody("B", occ_name="Crank:1")) == "Crank:1"
        assert ai._owning_occurrence_name(FakeBody("LooseBody")) == "LooseBody"


class TestInterferenceHandler:
    def test_reports_pairs_by_occurrence_with_volume(self):
        wheel = FakeBody("Body1", "Wheel:1")
        fork = FakeBody("Body1", "Fork:1")
        _install([FakeOcc("Wheel:1"), FakeOcc("Fork:1")],
                 [FakeResult(wheel, fork, 7.7)])
        out = _payload(ai.handler())
        assert out["clear"] is False and out["interference_count"] == 1
        pair = out["interferences"][0]
        assert {pair["occurrence_one"], pair["occurrence_two"]} == {"Wheel:1", "Fork:1"}
        assert pair["overlap_volume_cm3"] == 7.7

    def test_aggregates_volume_per_pair(self):
        # two interference bodies between the SAME pair -> summed into one entry
        a, b = FakeBody("B", "Crank:1"), FakeBody("B", "Wheel:1")
        _install([FakeOcc("Crank:1"), FakeOcc("Wheel:1")],
                 [FakeResult(a, b, 3.0), FakeResult(a, b, 2.0)])
        out = _payload(ai.handler())
        assert out["interference_count"] == 1
        assert out["interferences"][0]["overlap_volume_cm3"] == 5.0

    def test_clear_when_results_empty(self):
        _install([FakeOcc("A:1"), FakeOcc("B:1")], [])
        out = _payload(ai.handler())
        assert out["clear"] is True and out["interference_count"] == 0

    def test_short_circuits_under_two_occurrences(self):
        _install([FakeOcc("Solo:1")], [FakeResult(FakeBody("x"), FakeBody("y"), 1.0)])
        out = _payload(ai.handler())
        assert out["clear"] is True and "Fewer than 2" in out["note"]

    def test_pairs_sorted_by_descending_volume(self):
        # three distinct pairs with different overlap volumes -> reported largest-overlap first.
        a, b, c, d = (FakeBody("x", "A:1"), FakeBody("x", "B:1"),
                      FakeBody("x", "C:1"), FakeBody("x", "D:1"))
        _install([FakeOcc("A:1"), FakeOcc("B:1"), FakeOcc("C:1"), FakeOcc("D:1")],
                 [FakeResult(a, b, 1.0), FakeResult(c, d, 9.0), FakeResult(a, c, 4.0)])
        out = _payload(ai.handler())
        vols = [p["overlap_volume_cm3"] for p in out["interferences"]]
        assert vols == [9.0, 4.0, 1.0]            # strictly descending
        assert out["interference_count"] == 3

    def test_coincident_flag_echoed(self):
        _install([FakeOcc("A:1"), FakeOcc("B:1")], [])
        default = _payload(ai.handler())
        assert default["coincident_faces_included"] is False
        incl = _payload(ai.handler(include_coincident_faces=True))
        assert incl["coincident_faces_included"] is True

    def test_occurrences_checked_count(self):
        _install([FakeOcc("A:1"), FakeOcc("B:1"), FakeOcc("C:1")], [])
        assert _payload(ai.handler())["occurrences_checked"] == 3

    def test_owning_name_falls_back_when_parent_component_name_empty(self):
        # parentComponent present but its name is falsy -> use assemblyContext, then body name.
        b = FakeBody("BodyZ", comp_name="", occ_name="Crank:1")
        b.parentComponent = FakeOcc("")          # present object, empty name
        assert ai._owning_occurrence_name(b) == "Crank:1"

    def test_self_pair_note_when_same_occurrence_overlaps(self):
        # both bodies map to the same occurrence -> a self-pair (one entry, sorted key collapses).
        a, b = FakeBody("x", "Wheel:1"), FakeBody("y", "Wheel:1")
        _install([FakeOcc("Wheel:1"), FakeOcc("Other:1")], [FakeResult(a, b, 2.0)])
        out = _payload(ai.handler())
        pair = out["interferences"][0]
        assert pair["occurrence_one"] == "Wheel:1" and pair["occurrence_two"] == "Wheel:1"

    def test_no_design_errors(self):
        ai._common.design = lambda: None
        res = ai.handler()
        assert res["isError"] is True
