"""Unit tests for ``sketch_transform.py`` - sketch_move and sketch_copy.

Pinned: the ObjectCollection the two API calls demand (a plain list raises at the SWIG boundary),
the sketch-space transform matrix (translation scaled to centimetres, the measured rotation
convention, uniform scale about an anchor), the COORDINATE read-back that decides whether a move
really happened - the binding's contract is that the transform "respects any constraints that would
normally prohibit the move", so the bool cannot settle it - and the copy payload's new curve refs,
read off whichever sketch received them.
"""

import math
import types

import adsk.core
import pytest

from conftest import (FakeBoundingBox3D, FakePoint, assert_no_active_design, assert_unknown_units,
                      error_message, install, load_tool, make_design, make_sketch,
                      make_sketch_curve, payload)


def _rebox(curve):
    """Re-derive the bounding box from the endpoints, the way Fusion's is derived."""
    a, b = curve.startSketchPoint.geometry, curve.endSketchPoint.geometry
    curve.boundingBox = FakeBoundingBox3D(
        FakePoint(min(a.x, b.x), min(a.y, b.y), 0.0), FakePoint(max(a.x, b.x), max(a.y, b.y), 0.0))


def _boxed(token, x, y, dx=1.0, dy=0.0, length=10.0):
    """A sketch LINE running (x,y) -> (x+dx, y+dy): the start/end sketch points AND the bounding box
    they imply - the two samples entity_position reads."""
    curve = make_sketch_curve(token, length=length)
    curve.startSketchPoint = types.SimpleNamespace(geometry=FakePoint(x, y, 0.0))
    curve.endSketchPoint = types.SimpleNamespace(geometry=FakePoint(x + dx, y + dy, 0.0))
    _rebox(curve)
    return curve


def _matrix():
    """A Matrix3D: identity cells plus the setters the tool drives. setToRotation writes the 2D
    rotation block, setCell/getCell address the 4x4, and `translation` is a plain assignment."""
    cells = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    m = types.SimpleNamespace(cells=cells, translation=FakePoint(0.0, 0.0, 0.0), angle=None)

    def set_to_rotation(angle, axis, origin):
        m.angle = angle
        cells[0][0], cells[0][1] = math.cos(angle), -math.sin(angle)
        cells[1][0], cells[1][1] = math.sin(angle), math.cos(angle)
        return True

    def set_cell(row, col, value):
        cells[row][col] = value
        return True

    m.setToRotation = set_to_rotation
    m.setCell = set_cell
    m.getCell = lambda row, col: cells[row][col]
    return m


def _place(entity, matrix):
    """Apply the matrix to an entity's ENDPOINTS, then re-derive its box - a real move relocates the
    geometry and the box follows, which is why the box alone cannot see a symmetric transform."""
    t = matrix.translation
    for p in (entity.startSketchPoint.geometry, entity.endSketchPoint.geometry):
        x, y, z = p.x, p.y, p.z
        p.x = matrix.getCell(0, 0) * x + matrix.getCell(0, 1) * y + t.x
        p.y = matrix.getCell(1, 0) * x + matrix.getCell(1, 1) * y + t.y
        p.z = matrix.getCell(2, 2) * z + t.z
    _rebox(entity)


def _mover(store, moves=(), returns=True):
    """A Sketch.move that REFUSES a plain list the way the SWIG binding does, relocates the entities
    named in `moves` (all of them when it is None), and answers `returns` regardless."""
    def _move(collection, matrix):
        if isinstance(collection, (list, tuple)):
            raise TypeError("in method 'Sketch_move', argument 2 of type "
                            "'adsk::core::Ptr< adsk::core::ObjectCollection > const &'")
        store.append((collection, matrix))
        for i in range(collection.count):
            entity = collection.item(i)
            if moves is None or entity.entityToken in moves:
                _place(entity, matrix)
        return returns
    return _move


def _proxy_of(curve):
    """The assembly-context PROXY a component-owned sketch hands back from copy(), as measured: a
    different Python object whose assemblyContext names the occurrence and whose OWN entityToken is a
    different (248-char) token than the landed native curve's (192-char) - so neither identity nor
    token matches it. Only nativeObject bridges back to the landed curve."""
    token = getattr(curve, "entityToken", None)
    return types.SimpleNamespace(
        assemblyContext=types.SimpleNamespace(name="TokProbe:1"),
        entityToken=(token + "@occurrence-proxy") if token else None,
        nativeObject=curve, length=curve.length)


def _copier(store, target, made=(), extra_points=0, proxies=False, unreffable=()):
    """A Sketch.copy that lands `made` in `target` and returns them plus `extra_points` sketch
    points - the measured shape, where copying ONE line hands back three entities.

    proxies=True returns each landed curve as the assembly-context proxy a COMPONENT-owned sketch
    hands back (see _proxy_of); the default returns the native curve itself with nativeObject None,
    the measured root-owned shape. `unreffable` curves land in the flat sketchCurves collection only,
    the way a conic does - sketch_core: "This curve has NO '<type>:<index>' ref" - so they raise the
    curve COUNT while no ref can ever name them."""
    def _copy(collection, matrix, target_sketch=None):
        store.append((collection, matrix, target_sketch))
        landed = target_sketch if target_sketch is not None else target
        result = adsk.core.ObjectCollection.create()
        for curve, reffable in [(c, True) for c in made] + [(c, False) for c in unreffable]:
            curve.nativeObject = None          # a native entity reports no nativeObject
            landed.sketchCurves._items.append(curve)
            if reffable:
                landed.sketchCurves.sketchLines._items.append(curve)
            result.add(_proxy_of(curve) if proxies else curve)
        for i in range(extra_points):
            point = make_sketch_curve(f"PT{i}")
            point.nativeObject = None
            result.add(_proxy_of(point) if proxies else point)
        return result
    return _copy


@pytest.fixture
def mod():
    return load_tool("sketch_transform")


@pytest.fixture
def sketches(mod, monkeypatch):
    """'Plate' (line:0 at x=0, line:1 at x=5) and an empty 'Other'. Plate is created LAST, so the
    default (most recent) sketch is the one the tests drive."""
    plate = make_sketch("Plate", lines=[_boxed("L0", 0.0, 0.0), _boxed("L1", 5.0, 0.0)])
    second = make_sketch("Other", lines=[])
    design = make_design(sketches=[second, plate])
    install(mod, design)
    monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
    monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
    return plate, second


def _lines(sketch):
    return sketch.sketchCurves.sketchLines


def _corner(curve):
    return (round(curve.boundingBox.minPoint.x, 6), round(curve.boundingBox.minPoint.y, 6))


def _stale_item_at(monkeypatch, collection, index):
    """Make collection.item(index) RAISE - the stale entity proxy that yields no token."""
    intact = collection.item

    def item(i):
        if i == index:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return intact(i)
    monkeypatch.setattr(collection, "item", item)


class TestGuards:
    def test_no_active_design_is_a_clean_error(self, mod, sketches):
        assert_no_active_design(mod, mod.move_handler, entities="line:0", dx=10)

    def test_unknown_units_is_refused(self, mod, sketches):
        assert_unknown_units(mod.move_handler, entities="line:0", dx=10)

    def test_missing_entities_names_the_reference_form(self, mod, sketches):
        msg = error_message(mod.move_handler(entities="", dx=10))
        assert "'entities' is required" in msg and "'<type>:<index>'" in msg

    def test_an_unresolvable_ref_is_named(self, mod, sketches):
        msg = error_message(mod.move_handler(entities="line:0,arc:7", dx=10))
        assert "'arc:7'" in msg and "sketch_get" in msg

    def test_the_entities_selector_is_parsed_by_the_shared_resolver(self, mod, sketches):
        plate, _ = sketches
        msg = error_message(mod.move_handler(entities="line:0,arc:7", dx=10))
        assert msg == load_tool("_common").resolve_entity_refs(plate, "line:0,arc:7")[2]

    def test_a_shared_sketch_name_is_refused_in_the_multi_value_return_shape(
            self, mod, sketches, monkeypatch):
        # _prepare answers with 8 values, so the refusal has to travel in that same shape - a bare
        # error() here unpacks into a ValueError instead of reaching the caller. And the message
        # names the sketches that DO carry the name, never "No sketch named 'Plate'".
        refusal = "2 sketches are named 'Plate' ('Plate' in Root, 'Plate' in Frame)"
        monkeypatch.setattr(mod._common, "find_or_recent_sketch",
                            lambda d, n, remedy=None: (None, n, refusal))
        msg = error_message(mod.move_handler(sketch_name="Plate", entities="line:0", dx=10))
        assert msg == refusal and "No sketch named" not in msg

    def test_a_transform_that_asks_for_nothing_is_refused(self, mod, sketches):
        msg = error_message(mod.move_handler(entities="line:0"))
        assert "Nothing to apply" in msg and "rotation_deg" in msg

    def test_a_scale_factor_of_zero_is_refused_by_value(self, mod, sketches):
        msg = error_message(mod.move_handler(entities="line:0", scale_factor=0))
        assert "'scale_factor' must be greater than 0" in msg and "0.0" in msg

    def test_a_negative_scale_factor_is_refused_rather_than_mirroring(self, mod, sketches):
        msg = error_message(mod.move_handler(entities="line:0", scale_factor=-1))
        assert "greater than 0" in msg and "mirror" in msg

    def test_a_named_sketch_that_is_absent_lists_the_available_ones(self, mod, sketches):
        msg = error_message(mod.move_handler(sketch_name="Nope", entities="line:0", dx=1))
        assert "No sketch named 'Nope'" in msg and "Plate" in msg and "Other" in msg


class TestTheContainerAndTheMatrix:
    def test_move_is_handed_an_object_collection_not_a_plain_list(self, mod, sketches):
        plate, _ = sketches
        calls = []
        plate.move = _mover(calls, moves=None)
        payload(mod.move_handler(entities="line:0,line:1", dx=10))
        collection, _matrix_arg = calls[0]
        assert not isinstance(collection, (list, tuple))
        assert collection.count == 2
        assert [collection.item(i) for i in range(2)] == [_lines(plate).item(0),
                                                          _lines(plate).item(1)]

    def test_a_translation_is_scaled_from_display_units_to_centimetres(self, mod, sketches):
        plate, _ = sketches
        calls = []
        plate.move = _mover(calls, moves=None)
        payload(mod.move_handler(entities="line:0", dx=10, dy=-5, units="mm"))
        translation = calls[0][1].translation
        assert (round(translation.x, 6), round(translation.y, 6)) == (1.0, -0.5)

    def test_the_rotation_block_matches_the_measured_convention(self, mod, sketches):
        # measured: a 90 deg rotation about the sketch origin sends (2,0) to (0,2).
        plate, _ = sketches
        calls = []
        plate.move = _mover(calls, moves=None)
        payload(mod.move_handler(entities="line:0", rotation_deg=90))
        m = calls[0][1]
        x, y = 2.0, 0.0
        assert (round(m.getCell(0, 0) * x + m.getCell(0, 1) * y + m.translation.x, 6),
                round(m.getCell(1, 0) * x + m.getCell(1, 1) * y + m.translation.y, 6)) == (0.0, 2.0)

    def test_a_uniform_scale_is_taken_about_the_anchor_not_the_origin(self, mod, sketches):
        # scale x2 about (10 mm, 10 mm) = (1 cm, 1 cm) maps the point (2,1) cm to (3,1) cm.
        plate, _ = sketches
        calls = []
        plate.move = _mover(calls, moves=None)
        payload(mod.move_handler(entities="line:0", scale_factor=2, center_x=10, center_y=10))
        m = calls[0][1]
        assert m.getCell(0, 0) == pytest.approx(2.0) and m.getCell(2, 2) == pytest.approx(2.0)
        assert (round(m.translation.x, 6), round(m.translation.y, 6)) == (-1.0, -1.0)


class TestMoveIsJudgedByCoordinates:
    def test_a_pure_translation_is_reported_as_moved(self, mod, sketches):
        plate, _ = sketches
        plate.move = _mover([], moves=None)
        out = payload(mod.move_handler(entities="line:0", dx=10, dy=5))
        assert out["moved_entities"] == ["line:0"]
        assert _corner(_lines(plate).item(0)) == (1.0, 0.5)
        assert out["requested"] == {"units": "mm", "dx": 10.0, "dy": 5.0, "rotation_deg": 0.0,
                                    "scale_factor": 1.0}

    def test_a_true_return_with_unchanged_coordinates_is_an_error(self, mod, sketches):
        # the bool cannot settle it: the binding's contract is that the transform "respects any
        # constraints that would normally prohibit the move", so a refused entity and a moved
        # one are both reachable through a true return - only the coordinates tell them apart
        plate, _ = sketches
        plate.move = _mover([], moves=(), returns=True)
        msg = error_message(mod.move_handler(entities="line:0", dx=10))
        assert "move returned True" in msg
        assert "line:0 read the same coordinates afterwards" in msg
        # the cause is not guessed: both reachable explanations are named, and the remedy points at
        # a target sketch_delete_entity actually accepts
        assert "symmetric under" in msg and "respects any constraints" in msg
        assert "target='constraint:<index>'" in msg

    def test_a_false_return_is_reported_as_a_declined_move(self, mod, sketches):
        plate, _ = sketches
        plate.move = _mover([], moves=(), returns=False)
        msg = error_message(mod.move_handler(entities="line:0", dx=10))
        assert "declined the move" in msg and "returned false" in msg

    def test_one_entity_held_in_place_is_surfaced_as_a_partial_move(self, mod, sketches):
        plate, _ = sketches
        plate.move = _mover([], moves=("L0",))
        out = payload(mod.move_handler(entities="line:0,line:1", dx=10))
        assert out["moved_entities"] == ["line:0"] and out["unmoved_entities"] == ["line:1"]
        assert "Partial: line:1 read the same coordinates" in out["note"]

    def test_an_entity_whose_position_cannot_be_read_is_reported_unverified(self, mod, sketches):
        plate, _ = sketches
        line1 = _lines(plate).item(1)
        del line1.boundingBox, line1.startSketchPoint, line1.endSketchPoint
        plate.move = _mover([], moves=("L0",))
        out = payload(mod.move_handler(entities="line:0,line:1", dx=10))
        assert out["unverified_entities"] == ["line:1"] and "unmoved_entities" not in out
        assert "could not be re-read" in out["note"]

    def test_a_raising_move_reports_the_api_message(self, mod, sketches):
        plate, _ = sketches

        def _raise(collection, matrix):
            raise RuntimeError("3 : invalid argument sketchEntities")
        plate.move = _raise
        msg = error_message(mod.move_handler(entities="line:0", dx=10))
        assert "Could not move line:0" in msg and "invalid argument sketchEntities" in msg


class TestCopy:
    def test_copy_returns_the_new_curve_refs_and_the_append_note(self, mod, sketches):
        plate, _ = sketches
        calls = []
        plate.copy = _copier(calls, plate, made=[_boxed("L2", 9.0, 0.0)], extra_points=2)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"]
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 3
        # the returned collection carries the copied endpoints too - measured 3 for one line
        assert out["returned_entity_count"] == 3
        # an added curve APPENDS at the end of its kind's collection, so the ids in use keep their
        # entities; a note claiming a renumber sends the caller re-reading ids that never moved
        assert "APPENDS" in out["note"] and "RENUMBER" not in out["note"].upper()

    def test_a_same_sketch_copy_uses_the_two_argument_form(self, mod, sketches):
        plate, _ = sketches
        calls = []
        plate.copy = _copier(calls, plate, made=[_boxed("L2", 9.0, 0.0)])
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert calls[0][2] is None and out["target_sketch"] == "Plate"

    def test_a_cross_sketch_copy_verifies_the_target_not_the_source(self, mod, sketches):
        plate, second = sketches
        calls = []
        plate.copy = _copier(calls, plate, made=[_boxed("L9", 9.0, 0.0)])
        out = payload(mod.copy_handler(entities="line:0", target_sketch="Other", dx=10))
        assert calls[0][2] is second
        assert out["target_sketch"] == "Other" and out["new_curves"] == ["line:0"]
        assert out["curve_count_before"] == 0 and out["curve_count_after"] == 1
        assert _lines(plate).count == 2       # the source is untouched

    def test_a_copy_that_lands_nothing_in_the_target_is_an_error(self, mod, sketches):
        plate, second = sketches
        plate.copy = _copier([], plate, made=[], extra_points=2)
        msg = error_message(mod.copy_handler(entities="line:0", target_sketch="Other", dx=10))
        assert "still holds 0 curve(s)" in msg and "nothing landed" in msg

    def test_a_null_return_is_an_error_not_a_silent_success(self, mod, sketches):
        plate, _ = sketches
        plate.copy = lambda *args: None
        msg = error_message(mod.copy_handler(entities="line:0", dx=10))
        assert "copy returned no collection" in msg and "nothing was copied" in msg

    def test_an_unknown_target_sketch_lists_the_available_ones(self, mod, sketches):
        msg = error_message(mod.copy_handler(entities="line:0", target_sketch="Ghost", dx=10))
        assert "No sketch named 'Ghost'" in msg and "Other" in msg

    def test_a_shared_target_sketch_name_is_refused_with_its_owners(self, mod, sketches,
                                                                    monkeypatch):
        # The target_sketch lookup is its own design-wide resolve: a name SEVERAL sketches carry is
        # refused naming each owner, and nothing is copied - "No sketch named 'Other'" would state
        # the opposite of what the walk read.
        plate, _second = sketches
        calls = []
        plate.copy = _copier(calls, plate, made=[_boxed("L2", 9.0, 0.0)])
        refusal = "2 sketches are named 'Other' ('Other' in Root, 'Other' in Frame)"
        monkeypatch.setattr(mod._common, "find_sketch", lambda d, n, remedy=None: (None, refusal))
        msg = error_message(mod.copy_handler(entities="line:0", target_sketch="Other", dx=10))
        assert msg == refusal and "No sketch named" not in msg
        assert calls == []          # nothing was copied


# ── the 'component' / 'target_component' SCOPES ──────────────────────────────
# Fusion numbers sketches per component from 1, so two components each holding a "Plate" is the
# norm. sketch_copy takes TWO sketch names, so it takes two scopes: a refusal on 'target_sketch'
# that named 'component' would point at an input which does not narrow it. The REAL walk runs here.

@pytest.fixture
def shared_name(mod, monkeypatch):
    """One name across two components, DIFFERENT geometry: Alpha's 'Plate' has two lines starting
    at x=0 and x=5, Beta's has ONE at x=20. The moved corner says which sketch answered - two
    equal-sized sketches would hide a swapped source."""
    from conftest import MakeComp
    alpha_sk = make_sketch("Plate", lines=[_boxed("A0", 0.0, 0.0), _boxed("A1", 5.0, 0.0)])
    beta_sk = make_sketch("Plate", lines=[_boxed("B0", 20.0, 0.0)])
    alpha = MakeComp(name="Alpha", sketches=[alpha_sk])
    beta = MakeComp(name="Beta", sketches=[beta_sk])
    install(mod, make_design(comp=alpha, all_components=[alpha, beta]))
    monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
    monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
    return alpha_sk, beta_sk


class TestMoveComponentScope:
    def test_the_unscoped_shared_name_refuses_and_names_the_scope_input(self, mod, shared_name):
        alpha_sk, beta_sk = shared_name
        moves = []
        alpha_sk.move = _mover(moves, moves=None)
        beta_sk.move = _mover(moves, moves=None)
        msg = error_message(mod.move_handler(sketch_name="Plate", entities="line:0", dx=10))
        assert "2 sketches are named 'Plate'" in msg
        assert "'component'" in msg and "Rename one" not in msg
        assert moves == []

    def test_the_scope_moves_THAT_components_curve(self, mod, shared_name):
        alpha_sk, beta_sk = shared_name
        alpha_sk.move = _mover([], moves=None)
        beta_sk.move = _mover([], moves=None)
        payload(mod.move_handler(sketch_name="Plate", component="Beta", entities="line:0", dx=10))
        assert _corner(_lines(beta_sk).item(0)) == (21.0, 0.0)      # 20 + 10 mm = 21 cm
        assert _corner(_lines(alpha_sk).item(0)) == (0.0, 0.0)      # the sibling never moved

    def test_the_sibling_component_is_reachable_by_the_same_call(self, mod, shared_name):
        alpha_sk, beta_sk = shared_name
        alpha_sk.move = _mover([], moves=None)
        beta_sk.move = _mover([], moves=None)
        payload(mod.move_handler(sketch_name="Plate", component="Alpha", entities="line:0", dx=10))
        assert _corner(_lines(alpha_sk).item(0)) == (1.0, 0.0)
        assert _corner(_lines(beta_sk).item(0)) == (20.0, 0.0)

    def test_an_unknown_component_is_refused_before_the_move(self, mod, shared_name):
        alpha_sk, beta_sk = shared_name
        moves = []
        alpha_sk.move = _mover(moves, moves=None)
        msg = error_message(mod.move_handler(sketch_name="Plate", component="Gamma",
                                             entities="line:0", dx=10))
        assert "No component named 'Gamma'" in msg and moves == []

    def test_a_wrong_component_is_refused_even_when_the_name_is_UNIQUE(self, mod, monkeypatch):
        # The scope is VALIDATED: a dropped one moves Alpha's curve on a call that named Beta.
        from conftest import MakeComp
        alpha_sk = make_sketch("OnlyOne", lines=[_boxed("A0", 0.0, 0.0)])
        alpha = MakeComp(name="Alpha", sketches=[alpha_sk])
        beta = MakeComp(name="Beta", sketches=[])
        install(mod, make_design(comp=alpha, all_components=[alpha, beta]))
        monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
        monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        moves = []
        alpha_sk.move = _mover(moves, moves=None)
        msg = error_message(mod.move_handler(sketch_name="OnlyOne", component="Beta",
                                             entities="line:0", dx=10))
        assert "'Beta'" in msg and moves == []


class TestCopyTargetComponentScope:
    """'target_component' narrows the DESTINATION. It is a separate input because 'component'
    narrows the source, and a refusal has to name the one that would actually change the answer."""

    def test_a_shared_target_name_refuses_naming_target_component_not_component(self, mod,
                                                                                shared_name):
        alpha_sk, beta_sk = shared_name
        calls = []
        alpha_sk.copy = _copier(calls, alpha_sk, made=[_boxed("A2", 9.0, 0.0)])
        msg = error_message(mod.copy_handler(sketch_name="Plate", component="Alpha",
                                             entities="line:0", target_sketch="Plate", dx=10))
        assert "2 sketches are named 'Plate'" in msg
        assert "'target_component'" in msg and "Rename one" not in msg
        assert calls == []

    def test_target_component_selects_the_destination_sketch(self, mod, shared_name):
        alpha_sk, beta_sk = shared_name
        calls = []
        alpha_sk.copy = _copier(calls, alpha_sk, made=[_boxed("A2", 9.0, 0.0)])
        out = payload(mod.copy_handler(sketch_name="Plate", component="Alpha", entities="line:0",
                                       target_sketch="Plate", target_component="Beta", dx=10))
        # the destination handed to Sketch.copy is BETA's sketch, and the curve landed there
        assert calls[0][2] is beta_sk
        assert out["curve_count_after"] == 2 and len(_lines(alpha_sk)._items) == 2

    def test_an_unknown_target_component_is_refused_before_the_copy(self, mod, shared_name):
        alpha_sk, _beta_sk = shared_name
        calls = []
        alpha_sk.copy = _copier(calls, alpha_sk, made=[_boxed("A2", 9.0, 0.0)])
        msg = error_message(mod.copy_handler(sketch_name="Plate", component="Alpha",
                                             entities="line:0", target_sketch="Plate",
                                             target_component="Gamma", dx=10))
        assert "No component named 'Gamma'" in msg and calls == []

    def test_a_scoped_MISS_names_target_component_and_never_the_bare_component(self, mod,
                                                                               monkeypatch):
        # The scoped-miss refusal ("Retry with one of those as ...") has to name the input that
        # narrows THIS reference. 'component' narrows the SOURCE, so quoting it sends the caller to
        # an input that cannot change which destination was found.
        from conftest import MakeComp
        src = make_sketch("Source", lines=[_boxed("S0", 0.0, 0.0)])
        alpha_sk = make_sketch("Plate", lines=[_boxed("A0", 0.0, 0.0)])
        alpha = MakeComp(name="Alpha", sketches=[src, alpha_sk])
        beta = MakeComp(name="Beta", sketches=[make_sketch("Other", lines=[])])
        install(mod, make_design(comp=alpha, all_components=[alpha, beta]))
        monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
        monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        calls = []
        src.copy = _copier(calls, src, made=[_boxed("S2", 9.0, 0.0)])
        msg = error_message(mod.copy_handler(sketch_name="Source", entities="line:0",
                                             target_sketch="Plate", target_component="Beta",
                                             dx=10))
        assert "holds no sketch named 'Plate'" in msg
        assert "'target_component'" in msg
        assert "'component'" not in msg
        assert calls == []

    def test_an_AMBIGUOUS_target_component_names_target_component(self, mod, monkeypatch):
        # Component names are not unique either, and that refusal offers the occurrence-path
        # spelling - of the input that actually narrows the destination.
        from conftest import MakeComp, make_occurrence
        src = make_sketch("Source", lines=[_boxed("S0", 0.0, 0.0)])
        root = MakeComp(name="Root", sketches=[src])
        a = MakeComp(name="Frame", sketches=[make_sketch("Plate", lines=[])])
        b = MakeComp(name="Frame", sketches=[make_sketch("Plate", lines=[])])
        root.allOccurrences = [make_occurrence("P2-Gimbal:1+Frame:1", a),
                               make_occurrence("P3-Gimbal:1+Frame:1", b)]
        install(mod, make_design(comp=root, all_components=[root, a, b]))
        monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
        monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        calls = []
        src.copy = _copier(calls, src, made=[_boxed("S2", 9.0, 0.0)])
        msg = error_message(mod.copy_handler(sketch_name="Source", entities="line:0",
                                             target_sketch="Plate", target_component="Frame",
                                             dx=10))
        assert "2 components match 'Frame'" in msg
        assert "'target_component' also takes an occurrence fullPathName" in msg
        assert "'component'" not in msg
        assert calls == []

    def test_a_wrong_target_component_is_refused_even_when_the_target_name_is_UNIQUE(
            self, mod, monkeypatch):
        # The validation decision, at the ALTERNATE scope: a target_component quietly dropped
        # because 'Plate' happened to resolve copies into Alpha on a call that named Beta.
        from conftest import MakeComp
        src = make_sketch("Source", lines=[_boxed("S0", 0.0, 0.0)])
        alpha_sk = make_sketch("Plate", lines=[])
        alpha = MakeComp(name="Alpha", sketches=[src, alpha_sk])
        beta = MakeComp(name="Beta", sketches=[])
        install(mod, make_design(comp=alpha, all_components=[alpha, beta]))
        monkeypatch.setattr(adsk.core.Matrix3D, "create", lambda: _matrix())
        monkeypatch.setattr(adsk.core.Vector3D, "create", lambda x, y, z: FakePoint(x, y, z))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        calls = []
        src.copy = _copier(calls, src, made=[_boxed("S2", 9.0, 0.0)])
        msg = error_message(mod.copy_handler(sketch_name="Source", entities="line:0",
                                             target_sketch="Plate", target_component="Beta",
                                             dx=10))
        assert "'Beta'" in msg and calls == []


class TestCopyRefsCrossTheProxySeam:
    """A sketch owned by a COMPONENT hands the copy back as assembly-context PROXIES while its own
    collections hold the NATIVE curves. Measured: neither identity nor entityToken crosses that seam -
    the proxy's own 248-char token is a different token from the landed curve's 192-char one - so the
    refs are resolved through nativeObject, which matches the landed curve by identity AND token and
    reads None for an already-native entity."""

    def test_a_component_sketch_copy_still_reports_its_new_refs(self, mod, sketches):
        plate, _ = sketches
        plate.copy = _copier([], plate, made=[_boxed("L2", 9.0, 0.0)], extra_points=2, proxies=True)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"]
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 3
        assert "new_curves_complete" not in out

    def test_the_proxys_own_token_is_not_what_matched(self, mod, sketches):
        # the seam itself: the returned proxy's token is absent from the target's token map, so a
        # token-only read-back finds nothing - the ref can only have come through nativeObject
        plate, _ = sketches
        landed = _boxed("L2", 9.0, 0.0)
        plate.copy = _copier([], plate, made=[landed], proxies=True)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        proxy = _proxy_of(landed)
        assert proxy.entityToken != landed.entityToken
        assert proxy.entityToken not in mod._curve_ref_by_token(plate)
        assert out["new_curves"] == ["line:2"]

    def test_a_cross_sketch_component_copy_reads_the_target_back(self, mod, sketches):
        plate, second = sketches
        plate.copy = _copier([], plate, made=[_boxed("L8", 9.0, 0.0), _boxed("L9", 9.0, 2.0)],
                             proxies=True)
        out = payload(mod.copy_handler(entities="line:0", target_sketch="Other", dx=10))
        assert out["new_curves"] == ["line:0", "line:1"] and out["target_sketch"] == "Other"

    def test_a_root_sketch_copy_matches_on_the_entity_itself(self, mod, sketches):
        # nativeObject reads None for an already-native entity, so the same expression must fall
        # through to the returned entity - the root-owned case, where its token IS in the map
        plate, _ = sketches
        plate.copy = _copier([], plate, made=[_boxed("L2", 9.0, 0.0)], extra_points=2)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"] and "new_curves_complete" not in out

    def test_an_ambiguous_token_is_resolved_by_native_identity(self, mod, sketches):
        # the two pieces a split returns share ONE token, so the token map drops it - identity on
        # the NATIVE entity still names the exact curve rather than guessing between them
        plate, _ = sketches
        plate.copy = _copier([], plate, made=[_boxed("L0", 9.0, 0.0)], proxies=True)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"] and "new_curves_complete" not in out

    def test_a_curve_no_ref_addresses_is_reported_not_silently_dropped(self, mod, sketches):
        # a conic raises the curve count but carries no '<type>:<index>' ref at all - the count
        # delta is then the whole truth, and saying so beats handing back a bare []
        plate, _ = sketches
        plate.copy = _copier([], plate, made=[], unreffable=[_boxed("C0", 9.0, 0.0)], proxies=True)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == [] and out["new_curves_complete"] is False
        assert "1 curve(s) landed in 'Plate'" in out["note"]
        assert "NONE could be identified" in out["note"]
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 3

    def test_a_partial_read_back_names_what_it_found_and_admits_the_rest(self, mod, sketches):
        plate, _ = sketches
        plate.copy = _copier([], plate, made=[_boxed("L2", 9.0, 0.0)],
                             unreffable=[_boxed("C0", 9.0, 2.0)], proxies=True)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"] and out["new_curves_complete"] is False
        assert "2 curve(s) landed" in out["note"] and "only 1 could be identified" in out["note"]


class TestCurveRefIndexAlignment:
    """'<type>:<index>' is the ref the whole tool trades in - _common.resolve_entity_ref reads it
    back with coll.item(index) - so a curve the map cannot read must burn its index, not renumber
    the ones after it onto entities they do not name."""

    def test_an_unreadable_curve_leaves_the_later_refs_at_their_own_index(self, mod, sketches,
                                                                          monkeypatch):
        plate, _ = sketches
        tail = _boxed("L2", 9.0, 0.0)
        _lines(plate)._items.append(tail)
        plate.sketchCurves._items.append(tail)
        _stale_item_at(monkeypatch, _lines(plate), 1)
        refs = mod._curve_ref_by_token(plate)
        assert refs["L2"] == "line:2"                 # NOT line:1 - index 1 belongs to the bad curve
        assert refs == {"L0": "line:0", "L2": "line:2"}

    def test_the_copy_payload_names_the_new_curve_by_its_true_index(self, mod, sketches,
                                                                     monkeypatch):
        # the ref reaches the wire through copy's new_curves, where a renumbered index sends the
        # caller's next sketch_move at the wrong line
        plate, _ = sketches
        landed = _boxed("L2", 9.0, 0.0)
        plate.copy = _copier([], plate, made=[landed])
        _stale_item_at(monkeypatch, _lines(plate), 1)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"] and "new_curves_complete" not in out


class TestPostconditionWiring:
    def test_move_verifies_the_named_sketch(self, mod):
        assert [p.describe() for p in mod.move_item.handler.__wrapped__.__assert_postconditions__] \
            == ["sketch_curves_changed(sketch_name)"]

    def test_copy_verifies_the_target_sketch_before_the_source(self, mod):
        assert [p.describe() for p in mod.copy_item.handler.__wrapped__.__assert_postconditions__] \
            == ["sketch_curves_changed(target_sketch|sketch_name)"]

    def test_both_copy_scopes_are_declared_from_the_one_shared_factory(self, mod):
        # sketch_copy carries TWO component inputs. Both come from _sketch_detail, so they accept
        # the same three forms and are described in the same words - and the second names the
        # reference it narrows, which is the only thing telling a caller them apart.
        sd = load_tool("_sketch_detail")
        props = mod.copy_tool.input_schema["properties"]
        assert props["component"] == sd.COMPONENT_SCOPE[1]
        assert props["target_component"] == sd.component_scope("target_component",
                                                               narrows="target_sketch")[1]
        assert "'target_sketch'" in props["target_component"]["description"]

    def test_each_sketch_key_is_paired_with_the_scope_that_narrows_it(self, mod):
        # 'target_sketch' is narrowed by 'target_component' and 'sketch_name' by 'component'; the
        # pairing is POSITIONAL, so swapping the two would fingerprint the target sketch inside the
        # SOURCE's component and disclose a false unconfirmed on every copy across two components
        # that share a sketch name.
        move, = mod.move_item.handler.__wrapped__.__assert_postconditions__
        copy, = mod.copy_item.handler.__wrapped__.__assert_postconditions__
        assert move.keys == ("sketch_name",) and move.scope_keys == ("component",)
        assert copy.keys == ("target_sketch", "sketch_name")
        assert copy.scope_keys == ("target_component", "component")

    def test_the_target_key_selects_which_sketch_is_fingerprinted(self, mod, sketches):
        plate, second = sketches
        kind = load_tool("_assert").SketchCurvesChanged(keys=("target_sketch", "sketch_name"))
        before = kind.capture({"sketch_name": "Plate", "target_sketch": "Other"})
        assert before["marks"] == {}                          # 'Other' is empty, 'Plate' is not
        second.sketchCurves._items.append(_boxed("N0", 0.0, 0.0))
        reason, evidence = kind.verify({"sketch_name": "Plate", "target_sketch": "Other"},
                                       {}, before)
        assert reason == "" and evidence == {"curve_count_after": 1}


class TestTheSymmetryBlindSpot:
    def test_a_180_degree_rotation_about_a_line_midpoint_is_seen_as_a_move(self, mod, sketches):
        # MEASURED: a bounding box is invariant under any symmetry of what it bounds, so a line
        # rotated 180 deg about its own midpoint reads an IDENTICAL box while Fusion really moved
        # it. The endpoints swap, which is the only sample that tells the two apart.
        plate, _ = sketches
        line0 = _lines(plate).item(0)
        box_before = _corner(line0)
        plate.move = _mover([], moves=None)
        out = payload(mod.move_handler(entities="line:0", rotation_deg=180,
                                       center_x=5, center_y=0))
        assert _corner(line0) == box_before          # the box did NOT move - only the endpoints did
        assert out["moved_entities"] == ["line:0"] and "unmoved_entities" not in out

    def test_the_endpoints_are_part_of_the_shared_fingerprint(self, mod, sketches):
        plate, _ = sketches
        line0 = _lines(plate).item(0)
        before = load_tool("_assert").entity_position(line0)
        line0.startSketchPoint.geometry.x, line0.endSketchPoint.geometry.x = 1.0, 0.0
        _rebox(line0)
        after = load_tool("_assert").entity_position(line0)
        assert before != after and before[:2] == after[:2]    # same box, different endpoints


class TestPointsAreVerifiedToo:
    def test_a_point_only_move_is_reported_as_moved(self, mod, sketches):
        # a point-only selection touches no curve at all, so a curves-only fingerprint would call a
        # real move a no-op. entity_position reads a sketch point's (degenerate) box, measured.
        plate, _ = sketches
        pt = types.SimpleNamespace(entityToken="P0",
                                   geometry=FakePoint(0.0, 0.0, 0.0),
                                   boundingBox=FakeBoundingBox3D(FakePoint(0.0, 0.0, 0.0),
                                                                 FakePoint(0.0, 0.0, 0.0)))

        def _move_point(collection, matrix):
            t = matrix.translation
            for p in (pt.geometry, pt.boundingBox.minPoint, pt.boundingBox.maxPoint):
                p.x, p.y = p.x + t.x, p.y + t.y
            return True
        plate.sketchPoints._items.append(pt)
        plate.move = _move_point
        out = payload(mod.move_handler(entities="point:0", dx=10))
        assert out["moved_entities"] == ["point:0"]

    def test_the_postcondition_fingerprints_points_as_well_as_curves(self, mod, sketches):
        plate, _ = sketches
        pt = types.SimpleNamespace(entityToken="P0", geometry=FakePoint(0.0, 0.0, 0.0),
                                   boundingBox=FakeBoundingBox3D(FakePoint(0.0, 0.0, 0.0),
                                                                 FakePoint(0.0, 0.0, 0.0)))
        plate.sketchPoints._items.append(pt)
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        pt.geometry.x = 5.0
        pt.boundingBox.minPoint.x = pt.boundingBox.maxPoint.x = 5.0
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        # the curve count is unchanged - only a POINT moved - and that is still a real change
        assert reason == "" and evidence == {"curve_count_after": 2}


class TestPostconditionKeysAreChecked:
    def test_a_key_the_handler_does_not_take_is_refused_at_wiring_time(self, mod):
        # an unknown key reads None out of kwargs and the kind silently falls back to the
        # most-recent sketch, verifying the wrong one while reporting success
        _assert = load_tool("_assert")
        with pytest.raises(ValueError) as excinfo:
            _assert.wrap(mod.copy_handler,
                         [_assert.SketchCurvesChanged(keys=("target_sketch_name",))])
        assert "target_sketch_name" in str(excinfo.value)
        assert "copy_handler does not take" in str(excinfo.value)

    def test_the_real_keys_wire_cleanly(self, mod):
        _assert = load_tool("_assert")
        wrapped = _assert.wrap(mod.copy_handler,
                               [_assert.SketchCurvesChanged(keys=("target_sketch", "sketch_name"))])
        assert wrapped.__wrapped__ is mod.copy_handler
