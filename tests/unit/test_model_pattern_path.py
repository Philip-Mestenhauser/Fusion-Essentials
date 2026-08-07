"""Unit tests for model_pattern_path - pattern occurrences/bodies along a curve.

What is worth pinning without live Fusion: the quantity/units/distance/start_point guards each
refuse BEFORE any feature transaction opens; distance_type picks the measured PatternDistanceType
member; the distance is scaled to cm; the path is built on the component that OWNS the patterned
entities; startPoint/isSymmetric are set through the read-back helper and a setter the platform
ignores is an error, not a silent default; and the reported quantity is the feature's own
patternElements.count, with a mismatch failing the call.
"""

import types

import adsk.core
import adsk.fusion
import pytest

from conftest import (BRepEdge, MakeComp, error_message, install, load_tool, make_design,
                      make_sketch, make_sketch_curve, payload)

pp = load_tool("model_pattern_path")


# ── fakes: only the feature-collection graph this tool drives ────────────────────────────────────

class _Elements:
    def __init__(self, count):
        self.count = count


class _Feature:
    def __init__(self, name="Path-Pattern1", elements=3):
        self.name = name
        self.patternElements = _Elements(elements)


class _Input:
    """A PathPatternFeatureInput stand-in. `ignores` names properties whose assignment the platform
    silently drops - the exact shape set_verified exists to catch (a SWIG proxy accepts an
    assignment and keeps its default)."""
    def __init__(self, coll, path, quantity, distance, distance_type, ignores=()):
        self.inputEntities = coll
        self.path = path
        self.quantity = quantity
        self.distance = distance
        self.patternDistanceType = distance_type
        self.startPoint = 0.0
        self.isSymmetric = False
        self._ignores = set(ignores)

    def __setattr__(self, name, value):
        if name != "_ignores" and name in getattr(self, "_ignores", ()):
            return
        object.__setattr__(self, name, value)


class _PathPatternFeatures:
    def __init__(self, feature=None, ignores=(), input_is_none=False, raises=None):
        self.last_input = None
        self._feature = feature
        self._ignores = ignores
        self._input_is_none = input_is_none
        self._raises = raises

    def createInput(self, coll, path, quantity, distance, distance_type):
        if self._input_is_none:
            return None
        self.last_input = _Input(coll, path, quantity, distance, distance_type, self._ignores)
        return self.last_input

    def add(self, inp):
        if self._raises:
            raise RuntimeError(self._raises)
        return self._feature


def _wire(feature=None, ignores=(), tokens=None, sketches=(), input_is_none=False, raises=None,
          bodies=()):
    """A design whose root component owns the pattern feature collection AND the path factory."""
    if feature is None:
        feature = _Feature()
    ppf = _PathPatternFeatures(feature, ignores, input_is_none, raises)
    comp = MakeComp(bodies=bodies, sketches=sketches)
    comp.features = types.SimpleNamespace(
        pathPatternFeatures=ppf,
        createPath=lambda curve, is_chain=True: ("path", curve, is_chain))
    design = make_design(comp=comp, tokens=tokens or {})
    install(pp, design)
    return ppf


@pytest.fixture(autouse=True)
def _adsk(monkeypatch):
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal",
                        staticmethod(lambda v: ("real", v)), raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", BRepEdge, raising=False)


def _path_sketch(name="Spine", curves=1):
    return make_sketch(name=name, lines=[make_sketch_curve(f"c{i}") for i in range(curves)])


# ── guards: every refusal happens before a feature input is created ─────────────────────────────

def test_quantity_below_two_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=1, distance=10)
    assert "quantity must be >= 2" in error_message(res)
    assert ppf.last_input is None


def test_unknown_units_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3, distance=10,
                     units="furlong")
    assert "Unknown units" in error_message(res)
    assert ppf.last_input is None


def test_zero_distance_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3, distance=0)
    assert "non-zero" in error_message(res)
    assert ppf.last_input is None


def test_missing_distance_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3)
    assert "'distance' is required" in error_message(res)
    assert ppf.last_input is None


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_start_point_outside_zero_to_one_refused(bad):
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3, distance=10,
                     start_point=bad)
    msg = error_message(res)
    assert "between 0 and 1" in msg and str(bad) in msg      # names the offending value
    assert ppf.last_input is None


def test_non_numeric_start_point_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3, distance=10,
                     start_point="middle")
    assert "must be a number" in error_message(res)
    assert ppf.last_input is None


def test_unknown_distance_type_refused():
    ppf = _wire(sketches=[_path_sketch()])
    res = pp.handler(occurrences="Block:1", path="sketch:Spine", quantity=3, distance=10,
                     distance_type="proportional")
    assert "spacing, extent" in error_message(res)
    assert ppf.last_input is None


def test_no_targets_refused():
    _wire(sketches=[_path_sketch()])
    res = pp.handler(path="sketch:Spine", quantity=3, distance=10)
    assert "occurrences" in error_message(res)


def test_missing_path_refused():
    ppf = _wire(bodies=["Boss"])
    res = pp.handler(bodies="Boss", quantity=3, distance=10)
    assert "path" in error_message(res).lower()
    assert ppf.last_input is None


def test_empty_path_sketch_refused():
    ppf = _wire(bodies=["Boss"], sketches=[make_sketch(name="Spine")])
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10)
    assert "no curves" in error_message(res)
    assert ppf.last_input is None


# ── the values that reach createInput ────────────────────────────────────────────────────────────

def test_distance_scaled_to_cm():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=30, units="mm"))
    assert ppf.last_input.distance == ("real", 3.0)          # 30 mm -> 3 cm
    assert ppf.last_input.quantity == ("real", 3)


def test_inches_scaled():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=1, units="in"))
    assert abs(ppf.last_input.distance[1] - 2.54) < 1e-9


def test_negative_distance_refused():
    # the only documented reverse control is isFlipDirection, which this tool does not expose, so a
    # negative distance is refused rather than passed through as an unproven direction flip
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=-10)
    msg = error_message(res)
    assert "'distance' must be positive" in msg and "-10" in msg
    assert ppf.last_input is None


def test_spacing_is_the_default_distance_type():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10))
    assert ppf.last_input.patternDistanceType == \
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
    assert out["distance_type"] == "spacing"


def test_extent_distance_type_selected():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10,
                             distance_type="extent"))
    assert ppf.last_input.patternDistanceType == \
        adsk.fusion.PatternDistanceType.ExtentPatternDistanceType
    assert out["distance_type"] == "extent"


def test_path_is_built_from_the_sketch_curves():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch(curves=3)])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10))
    kind, _curve, is_chain = ppf.last_input.path
    assert kind == "path" and is_chain is True               # the connected curves are chained
    assert out["path"] == "sketch:Spine"


def test_single_edge_handle_is_chained():
    edge = BRepEdge(curve=None)
    ppf = _wire(bodies=["Boss"], tokens={"E1": edge})
    out = payload(pp.handler(bodies="Boss", path="E1", quantity=3, distance=10))
    _kind, curve, is_chain = ppf.last_input.path
    assert curve is edge and is_chain is True
    assert out["path"] == "1 edge(s)"


def test_start_point_and_symmetric_reach_the_input():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10,
                             start_point=0.25, symmetric=True))
    assert ppf.last_input.startPoint == 0.25
    assert ppf.last_input.isSymmetric is True
    assert out["start_point"] == 0.25 and out["symmetric"] is True


def test_start_point_defaults_to_the_path_start():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10))
    assert ppf.last_input.startPoint == 0.0 and out["start_point"] == 0.0


# ── honesty: a dropped setting, a dropped input, a wrong count ───────────────────────────────────

def test_ignored_start_point_is_an_error_not_a_silent_default():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()], ignores=("startPoint",))
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10, start_point=0.4)
    msg = error_message(res)
    assert "start_point" in msg and "No pattern was created" in msg


def test_ignored_symmetric_is_an_error():
    ppf = _wire(bodies=["Boss"], sketches=[_path_sketch()], ignores=("isSymmetric",))
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10, symmetric=True)
    assert "symmetric" in error_message(res)


def test_null_create_input_is_an_error():
    _wire(bodies=["Boss"], sketches=[_path_sketch()], input_is_none=True)
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10)
    assert "createInput returned nothing" in error_message(res)


def test_null_feature_is_an_error():
    _wire(feature=False, bodies=["Boss"], sketches=[_path_sketch()])
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10)
    assert "no feature" in error_message(res)


def test_add_failure_is_reported():
    _wire(bodies=["Boss"], sketches=[_path_sketch()], raises="path is not connected")
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10)
    assert "path is not connected" in error_message(res)


def test_instance_count_is_read_back_off_the_feature():
    _wire(feature=_Feature(elements=5), bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=5, distance=10))
    assert out["quantity"] == 5 and out["patterned"] is True


def test_wrong_instance_count_fails_the_call():
    _wire(feature=_Feature(elements=2), bodies=["Boss"], sketches=[_path_sketch()])
    res = pp.handler(bodies="Boss", path="sketch:Spine", quantity=6, distance=10)
    msg = error_message(res)
    assert "created 2 instances but 6 were requested" in msg
    assert "design_delete_feature" in msg


def test_note_states_that_copies_do_not_rotate_along_the_path():
    # isOrientationAlongPath is False by default (measured) - nothing else in the payload shows it
    _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10))
    assert "do not rotate to follow the path" in out["note"]


def test_body_targets_reported_as_bodies():
    _wire(bodies=["Boss"], sketches=[_path_sketch()])
    out = payload(pp.handler(bodies="Boss", path="sketch:Spine", quantity=3, distance=10))
    assert out["entity_kind"] == "bodies" and out["entities"] == ["Boss"]
