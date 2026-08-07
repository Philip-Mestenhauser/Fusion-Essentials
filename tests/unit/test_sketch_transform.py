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
    def test_copy_returns_the_new_curve_refs_and_the_renumbering_warning(self, mod, sketches):
        plate, _ = sketches
        calls = []
        plate.copy = _copier(calls, plate, made=[_boxed("L2", 9.0, 0.0)], extra_points=2)
        out = payload(mod.copy_handler(entities="line:0", dx=10))
        assert out["new_curves"] == ["line:2"]
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 3
        # the returned collection carries the copied endpoints too - measured 3 for one line
        assert out["returned_entity_count"] == 3
        assert "RENUMBERS" in out["note"]

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


class TestPostconditionWiring:
    def test_move_verifies_the_named_sketch(self, mod):
        assert [p.describe() for p in mod.move_item.handler.__wrapped__.__assert_postconditions__] \
            == ["sketch_curves_changed(sketch_name)"]

    def test_copy_verifies_the_target_sketch_before_the_source(self, mod):
        assert [p.describe() for p in mod.copy_item.handler.__wrapped__.__assert_postconditions__] \
            == ["sketch_curves_changed(target_sketch|sketch_name)"]

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
