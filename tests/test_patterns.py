"""Unit tests for ``patterns.py`` — rectangular & circular component patterns.

The logic worth pinning (no live Fusion): occurrence resolution (exact, then
substring; missing names reported), the world-axis lookup (x/y/z →
construction axis), spacing → cm scaling, the quantity guards, and that the
right values reach the pattern-feature input (quantityOne/distanceOne, the
optional second direction, circular quantity/totalAngle). Feature creation is
captured on fake rectangular/circular pattern feature collections.
"""

import json

from conftest import load_tool

pt = load_tool("patterns")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name


class FakeObjectCollection:
    def __init__(self):
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def add(self, item):
        self._items.append(item)


class FakeRectInput:
    def __init__(self, coll, d1, q1, dist1, dist_type):
        self.coll = coll
        self.d1 = d1
        self.q1 = q1
        self.dist1 = dist1
        self.dist_type = dist_type
        self.dir_two = None

    def setDirectionTwo(self, d2, q2, dist2):
        self.dir_two = (d2, q2, dist2)


class FakeRectFeatures:
    def __init__(self):
        self.last_input = None

    def createInput(self, coll, d1, q1, dist1, dist_type):
        self.last_input = FakeRectInput(coll, d1, q1, dist1, dist_type)
        return self.last_input

    def add(self, inp):
        return type("F", (), {"name": "R-Pattern1"})()


class FakeCircInput:
    def __init__(self, coll, axis):
        self.coll = coll
        self.axis = axis
        self.quantity = None
        self.totalAngle = None
        self.isSymmetric = False


class FakeCircFeatures:
    def __init__(self):
        self.last_input = None

    def createInput(self, coll, axis):
        self.last_input = FakeCircInput(coll, axis)
        return self.last_input

    def add(self, inp):
        return type("F", (), {"name": "C-Pattern1"})()


class FakeRoot:
    def __init__(self, occurrences, rf, cf):
        self.allOccurrences = list(occurrences)
        self.xConstructionAxis = "AXIS_X"
        self.yConstructionAxis = "AXIS_Y"
        self.zConstructionAxis = "AXIS_Z"
        self.features = type("F", (), {"rectangularPatternFeatures": rf,
                                       "circularPatternFeatures": cf})()


class FakeDesign:
    def __init__(self, occurrences, rf, cf):
        self.rootComponent = FakeRoot(occurrences, rf, cf)


def _install(occ_names):
    rf, cf = FakeRectFeatures(), FakeCircFeatures()
    occs = [FakeOcc(n) for n in occ_names]
    design = FakeDesign(occs, rf, cf)
    pt.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ObjectCollection.create = staticmethod(FakeObjectCollection)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
    pdt = adsk.fusion.PatternDistanceType
    pdt.SpacingPatternDistanceType = "Spacing"
    pdt.ExtentPatternDistanceType = "Extent"
    return rf, cf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── occurrence resolution (shared) ──────────────────────────────────────────

class TestResolution:
    def test_exact_name(self):
        rf, _ = _install(["Block:1", "Other:1"])
        out = _payload(pt.rectangular_handler(occurrences="Block:1", quantity_one=2, spacing_one=10))
        assert out["occurrences"] == ["Block:1"]

    def test_substring_fallback(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.rectangular_handler(occurrences="block", quantity_one=2, spacing_one=10))
        assert out["occurrences"] == ["Block:1"]

    def test_missing_reported(self):
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Ghost", quantity_one=2, spacing_one=10)
        assert res["isError"] is True and "No occurrence matched: Ghost" in res["message"]

    def test_comma_separated_multiple(self):
        _install(["A:1", "B:1"])
        out = _payload(pt.rectangular_handler(occurrences="A:1, B:1", quantity_one=2, spacing_one=10))
        assert set(out["occurrences"]) == {"A:1", "B:1"}


# ── rectangular ──────────────────────────────────────────────────────────────

class TestRectangular:
    def test_single_direction_scales_spacing(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.rectangular_handler(occurrences="Block:1", quantity_one=3,
                                              spacing_one=30, direction_one="x", units="mm"))
        assert out["total_instances"] == 3
        inp = rf.last_input
        assert inp.d1 == "AXIS_X"
        assert inp.q1 == ("real", 3)
        assert inp.dist1[0] == "real" and abs(inp.dist1[1] - 3.0) < 1e-9   # 30 mm -> 3 cm
        assert inp.dir_two is None               # no second direction
        assert inp.dist_type == "Spacing"

    def test_two_directions(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.rectangular_handler(occurrences="Block:1", quantity_one=3, spacing_one=30,
                                              direction_one="x", quantity_two=2, spacing_two=20,
                                              direction_two="y", units="mm"))
        assert out["total_instances"] == 6
        d2, q2, dist2 = rf.last_input.dir_two
        assert d2 == "AXIS_Y"
        assert q2 == ("real", 2)
        assert dist2[0] == "real" and abs(dist2[1] - 2.0) < 1e-9   # 20 mm -> 2 cm

    def test_quantity_one_must_be_positive(self):
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Block:1", quantity_one=0, spacing_one=10)
        assert res["isError"] is True and "quantity_one must be >= 1" in res["message"]

    def test_unknown_direction(self):
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="w")
        assert res["isError"] is True and "Unknown direction_one 'w'" in res["message"]


# ── circular ─────────────────────────────────────────────────────────────────

class TestCircular:
    def test_basic_full_ring(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.circular_handler(occurrences="Spoke:1", quantity=6,
                                           total_angle_deg=360, axis="z"))
        assert out["quantity"] == 6
        inp = cf.last_input
        assert inp.axis == "AXIS_Z"
        assert inp.quantity == ("real", 6)
        assert inp.totalAngle == ("str", "360.0 deg")

    def test_axis_selection(self):
        _, cf = _install(["Spoke:1"])
        _payload(pt.circular_handler(occurrences="Spoke:1", quantity=4, axis="y"))
        assert cf.last_input.axis == "AXIS_Y"

    def test_symmetric_flag(self):
        _, cf = _install(["Spoke:1"])
        _payload(pt.circular_handler(occurrences="Spoke:1", quantity=4, symmetric=True))
        assert cf.last_input.isSymmetric is True

    def test_quantity_must_be_at_least_two(self):
        _install(["Spoke:1"])
        res = pt.circular_handler(occurrences="Spoke:1", quantity=1)
        assert res["isError"] is True and "quantity must be >= 2" in res["message"]

    def test_unknown_axis(self):
        _install(["Spoke:1"])
        res = pt.circular_handler(occurrences="Spoke:1", quantity=4, axis="w")
        assert res["isError"] is True and "Unknown axis 'w'" in res["message"]
