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

pt = load_tool("model_pattern")


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

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


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
    pt._common.app = pt.app
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
        assert out["entities"] == ["Block:1"]

    def test_substring_fallback(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.rectangular_handler(occurrences="block", quantity_one=2, spacing_one=10))
        assert out["entities"] == ["Block:1"]

    def test_missing_reported(self):
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Ghost", quantity_one=2, spacing_one=10)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_comma_separated_multiple(self):
        _install(["A:1", "B:1"])
        out = _payload(pt.rectangular_handler(occurrences="A:1, B:1", quantity_one=2, spacing_one=10))
        assert set(out["entities"]) == {"A:1", "B:1"}


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

    def test_unknown_units(self):
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unknown_direction_two_errors(self):
        # quantity_two>1 forces direction_two resolution; a bad axis must error
        _install(["Block:1"])
        res = pt.rectangular_handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     quantity_two=2, spacing_two=5, direction_two="w")
        assert res["isError"] is True and "Unknown direction_two 'w'" in res["message"]

    def test_single_row_direction_two_none_in_payload(self):
        _install(["Block:1"])
        out = _payload(pt.rectangular_handler(occurrences="Block:1", quantity_one=4, spacing_one=10,
                                              quantity_two=1))
        # quantity_two==1 -> direction_two reported as None, total = quantity_one
        assert out["direction_two"] is None
        assert out["total_instances"] == 4

    def test_spacing_scaled_inches(self):
        rf, _ = _install(["Block:1"])
        _payload(pt.rectangular_handler(occurrences="Block:1", quantity_one=2, spacing_one=1,
                                        units="in"))
        # 1in -> 2.54cm
        assert abs(rf.last_input.dist1[1] - 2.54) < 1e-9


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

    def test_partial_arc_angle_string(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.circular_handler(occurrences="Spoke:1", quantity=3, total_angle_deg=90))
        # the angle is formatted as a "<float> deg" ValueInput string and echoed in payload
        assert cf.last_input.totalAngle == ("str", "90.0 deg")
        assert out["total_angle_deg"] == 90.0

    def test_symmetric_defaults_false(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.circular_handler(occurrences="Spoke:1", quantity=4))
        assert cf.last_input.isSymmetric is False
        assert out["symmetric"] is False


# ── 'bodies' targets (BodyRefList) — pattern solid bodies, not occurrences ──────────────────────

class _FakeBody:
    def __init__(self, name):
        self.name = name


def _install_with_bodies(body_map):
    """Install a design that also resolves body handles/names (the app-reference seam).

    The handler now resolves its design via _common.design() (the SAME seam _inputs uses), so there is
    ONE design. We take _install's rich FakeDesign (its root has the construction axes the handler
    needs) and EXTEND its root with body-by-name + findEntityByToken, then point both _common.design
    and _inputs._common.design at it."""
    rf, cf = _install([])
    import adsk.fusion
    adsk.fusion.BRepBody = _FakeBody
    design = pt.app.activeProduct                 # rich FakeDesign with rootComponent + axes
    root = design.rootComponent
    root.bRepBodies = type("BB", (), {
        "itemByName": staticmethod(lambda n: body_map.get(n)),
        "count": len(body_map), "item": staticmethod(lambda i: list(body_map.values())[i]),
    })()
    design.findEntityByToken = lambda t, bm=body_map: ([bm[t]] if t in bm else [])
    pt._common.design = lambda: design
    pt._common.target_component = lambda x: root
    pt._inputs._common.design = lambda: design
    pt._inputs._common.target_component = lambda x: root
    return rf, cf


class TestBodyTargets:
    def test_rectangular_patterns_bodies_by_name(self):
        rf, _ = _install_with_bodies({"Boss": _FakeBody("Boss")})
        out = _payload(pt.rectangular_handler(bodies="Boss", quantity_one=3, spacing_one=10))
        assert out["entity_kind"] == "bodies"
        assert out["entities"] == ["Boss"]
        assert out["total_instances"] == 3

    def test_circular_patterns_bodies_by_handle(self):
        h = "/v" + "B" * 70
        rf, _ = _install_with_bodies({h: _FakeBody("FromHandle")})
        out = _payload(pt.circular_handler(bodies=h, quantity=4))
        assert out["entity_kind"] == "bodies"
        assert out["entities"] == ["FromHandle"]

    def test_bodies_take_precedence_over_occurrences(self):
        rf, _ = _install_with_bodies({"Boss": _FakeBody("Boss")})
        out = _payload(pt.rectangular_handler(occurrences="ignored", bodies="Boss",
                                              quantity_one=2, spacing_one=5))
        assert out["entity_kind"] == "bodies" and out["entities"] == ["Boss"]

    def test_bad_body_name_errors(self):
        _install_with_bodies({"Boss": _FakeBody("Boss")})
        res = pt.rectangular_handler(bodies="Nope", quantity_one=2, spacing_one=5)
        assert res["isError"] is True and "Nope" in res["message"]


# ── bug #4 regression: a body in a SUB-COMPONENT must pattern on THAT component ──────────────────

class _BodyInSub:
    """A body whose parentComponent is a distinct sub-component (its OWN axes + pattern features).
    The old code took the axis + built the feature on ROOT, mismatching the body's object path —
    Fusion raised 'InternalValidationError getObjectPath'. _owning_component must now resolve to the
    body's parent."""
    def __init__(self, name, parent):
        self.name = name
        self.parentComponent = parent


def _install_body_in_subcomponent():
    rf, cf = _install([])           # installs adsk fakes (ValueInput, ObjectCollection, axes enum)
    import adsk.fusion
    adsk.fusion.BRepBody = _BodyInSub
    # the SUB-component that owns the body — distinct axes + its OWN pattern-feature collections
    sub_rf, sub_cf = FakeRectFeatures(), FakeCircFeatures()
    sub = type("Sub", (), {
        "xConstructionAxis": "SUB_X", "yConstructionAxis": "SUB_Y", "zConstructionAxis": "SUB_Z",
        "features": type("F", (), {"rectangularPatternFeatures": sub_rf,
                                   "circularPatternFeatures": sub_cf})(),
    })()
    body = _BodyInSub("SubBoss", sub)
    comp = type("C", (), {"bRepBodies": type("BB", (), {
        "itemByName": staticmethod(lambda n: body if n == "SubBoss" else None),
        "count": 1, "item": staticmethod(lambda i: body)})()})()
    # root carries DIFFERENT axes so a mistaken root build would be detectable
    root_rf, root_cf = FakeRectFeatures(), FakeCircFeatures()
    root = type("Root", (), {
        "xConstructionAxis": "ROOT_X", "yConstructionAxis": "ROOT_Y", "zConstructionAxis": "ROOT_Z",
        "features": type("F", (), {"rectangularPatternFeatures": root_rf,
                                   "circularPatternFeatures": root_cf})()})()

    class _D:
        rootComponent = root
        def findEntityByToken(self, t):
            return []
    d = _D()
    pt._inputs._common.design = lambda: d
    pt._inputs._common.target_component = lambda x: comp
    # the tool's own _design() must see this design too
    pt.app = type("A", (), {"activeProduct": d})()
    pt._common.app = pt.app
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _D) else None
    return sub_rf, sub_cf, root_rf, root_cf


class TestBodyOwningComponent:
    def test_circular_builds_on_bodys_parent_component(self):
        sub_rf, sub_cf, root_rf, root_cf = _install_body_in_subcomponent()
        out = _payload(pt.circular_handler(bodies="SubBoss", quantity=6, axis="y"))
        assert out["entity_kind"] == "bodies"
        # the feature was created on the SUB-component (axis from the sub, not root)
        assert sub_cf.last_input is not None and sub_cf.last_input.axis == "SUB_Y"
        assert root_cf.last_input is None          # root was NOT used (the old bug)

    def test_rectangular_builds_on_bodys_parent_component(self):
        sub_rf, sub_cf, root_rf, root_cf = _install_body_in_subcomponent()
        _payload(pt.rectangular_handler(bodies="SubBoss", quantity_one=3, spacing_one=10,
                                        direction_one="x"))
        assert sub_rf.last_input is not None and sub_rf.last_input.d1 == "SUB_X"
        assert root_rf.last_input is None
