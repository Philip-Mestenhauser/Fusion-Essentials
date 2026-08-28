"""Unit tests for ``_geom.py`` - the direction-vector math find_geometry and sys_get_selection
share: normalizing a Vector3D (``unit_vector``), the unit direction between two points
(``unit_vector_between``), and a face's evaluator-sampled normal (``evaluator_normal_at``); plus the
owning-body walk and the before/after volume and face-count samples every effect check reads through
(``owning_bodies`` / ``volumes`` / ``volume_delta`` / ``face_counts`` / ``face_count_delta``).
"""

import math

from conftest import (BRepBody, BRepFace, FakeBoundingBox3D, FakePoint, FakeVector3D, entity_proxy,
                      load_tool)

geom = load_tool("_geom")


# ── unit_vector: normalization + zero-vector guard ─────────────────────────

class TestUnitVector:
    def test_normalizes_to_length_one(self):
        assert geom.unit_vector(FakeVector3D(0, 0, 5)) == [0.0, 0.0, 1.0]

    def test_arbitrary_vector_normalized(self):
        u = geom.unit_vector(FakeVector3D(3, 4, 0))  # length 5
        assert u == [0.6, 0.8, 0.0]
        assert math.isclose(math.sqrt(sum(c * c for c in u)), 1.0, abs_tol=1e-9)

    def test_zero_vector_returns_none(self):
        # A zero-length vector has no direction — must be None, not [0,0,0].
        assert geom.unit_vector(FakeVector3D(0, 0, 0)) is None

    def test_none_input_returns_none(self):
        assert geom.unit_vector(None) is None

    def test_decimals_controls_rounding(self):
        v = FakeVector3D(1, 2, 2)  # length 3 -> (1/3, 2/3, 2/3)
        assert geom.unit_vector(v, decimals=2) == [0.33, 0.67, 0.67]
        assert geom.unit_vector(v, decimals=4) == [0.3333, 0.6667, 0.6667]

    def test_already_unit_vector_is_unchanged(self):
        # Renormalizing an already-unit vector (e.g. an evaluator normal) must be a no-op.
        assert geom.unit_vector(FakeVector3D(0, 1, 0)) == [0.0, 1.0, 0.0]


# ── unit_vector_between: point-to-point direction ───────────────────────────

class TestUnitVectorBetween:
    def test_axis_aligned_direction(self):
        d = geom.unit_vector_between(FakePoint(0, 0, 0), FakePoint(3, 0, 0))
        assert d == [1.0, 0.0, 0.0]

    def test_diagonal_direction_is_normalized(self):
        # a 3-4-0 delta -> unit direction [0.6, 0.8, 0]
        d = geom.unit_vector_between(FakePoint(0, 0, 0), FakePoint(3, 4, 0))
        assert d == [0.6, 0.8, 0.0]

    def test_direction_is_independent_of_translation(self):
        # only the DELTA matters, not the absolute positions.
        d = geom.unit_vector_between(FakePoint(10, 10, 10), FakePoint(13, 10, 10))
        assert d == [1.0, 0.0, 0.0]

    def test_same_point_returns_none(self):
        # zero-length delta has no direction.
        assert geom.unit_vector_between(FakePoint(1, 1, 1), FakePoint(1, 1, 1)) is None

    def test_none_endpoints_return_none(self):
        assert geom.unit_vector_between(None, FakePoint(1, 0, 0)) is None
        assert geom.unit_vector_between(FakePoint(0, 0, 0), None) is None

    def test_decimals_controls_rounding(self):
        d = geom.unit_vector_between(FakePoint(0, 0, 0), FakePoint(1, 2, 2), decimals=2)
        assert d == [0.33, 0.67, 0.67]


# ── evaluator_normal_at: the getNormalAtPoint sample ────────────────────────

class _FakeEvaluator:
    """Stands in for a BRepFace SurfaceEvaluator. getNormalAtPoint returns (success, normal) - the
    Python shape of a bool-return + output-normal API."""
    def __init__(self, normal=None, ok=True, raises=False):
        self._normal = normal
        self._ok = ok
        self._raises = raises

    def getNormalAtPoint(self, point):
        if self._raises:
            raise RuntimeError("point is off the face surface")
        return (self._ok, FakeVector3D(*self._normal) if self._normal else None)


class _FakeFace:
    def __init__(self, evaluator=None):
        self.evaluator = evaluator


class TestEvaluatorNormalAt:
    def test_returns_the_sampled_normal(self):
        face = _FakeFace(_FakeEvaluator(normal=(0, 0, 1)))
        assert geom.evaluator_normal_at(face, FakePoint(0, 0, 0)) == [0.0, 0.0, 1.0]

    def test_none_point_returns_none(self):
        face = _FakeFace(_FakeEvaluator(normal=(0, 0, 1)))
        assert geom.evaluator_normal_at(face, None) is None

    def test_missing_evaluator_returns_none(self):
        face = _FakeFace(evaluator=None)
        assert geom.evaluator_normal_at(face, FakePoint(0, 0, 0)) is None

    def test_evaluator_raising_returns_none(self):
        # off-surface sample point -> getNormalAtPoint raises -> degrades to None, never a crash.
        face = _FakeFace(_FakeEvaluator(raises=True))
        assert geom.evaluator_normal_at(face, FakePoint(0, 0, 0)) is None

    def test_failed_okflag_returns_none(self):
        face = _FakeFace(_FakeEvaluator(normal=(1, 0, 0), ok=False))
        assert geom.evaluator_normal_at(face, FakePoint(0, 0, 0)) is None

    def test_decimals_controls_rounding(self):
        face = _FakeFace(_FakeEvaluator(normal=(1, 2, 2)))
        assert geom.evaluator_normal_at(face, FakePoint(0, 0, 0), decimals=2) == [0.33, 0.67, 0.67]


# ── body_aabb: the bodies-only AABB every occurrence/component size read shares ─────────────────
#
# An Occurrence/Component's plain .boundingBox also counts visible sketches + construction datums,
# so an orphaned oversized sketch inflates the box (live-verified: a 68x10 body read 120x120).
# body_aabb must call boundingBox2 with the SOLID|SURFACE|MESH body types instead; a BRepBody has no
# boundingBox2 and its own .boundingBox is already body-only.

class TestBodyAabb:
    def test_occurrence_uses_boundingBox2_with_body_types(self):
        class _Ent:
            boundingBox = "whole_box"          # sketch/datum-inflated - must NOT be used
            def __init__(self):
                self.calls = []
            def boundingBox2(self, types):
                self.calls.append(types)
                return "body_box"
        e = _Ent()
        assert geom.body_aabb(e) == "body_box"
        assert e.calls == [geom._BODY_BBOX_TYPES]

    def test_body_types_exclude_sketch_and_construction(self):
        # the bitmask spans solid|surface|mesh only (1|2|4=7) - sketch(8)/construction bits are out.
        import adsk.fusion
        t = adsk.fusion.BoundingBoxEntityTypes
        assert geom._BODY_BBOX_TYPES == (t.SolidBRepBodyBoundingBoxEntityType
                                         | t.SurfaceBodyBoundingBoxEntityType
                                         | t.MeshBodyBoundingBoxEntityType)
        assert not (geom._BODY_BBOX_TYPES & t.SketchBoundingBoxEntityType)

    def test_body_falls_back_to_plain_boundingBox(self):
        class _Body:
            boundingBox = "solid_box"          # no boundingBox2 attribute -> fallback
        assert geom.body_aabb(_Body()) == "solid_box"

    def test_no_body_geometry_returns_none(self):
        class _Empty:
            def boundingBox2(self, types):
                return None                    # no measurable bodies
        assert geom.body_aabb(_Empty()) is None


# ── owning_bodies: deduped by entityToken, NEVER by identity ────────────────

def _entities_on_one_body(count, token="Body1", face_count=6, kind="face"):
    """`count` faces (or edge-shaped stubs) of ONE body, each holding its OWN proxy - the measured
    shape of face.body / edge.body (see conftest entity_proxy)."""
    body = BRepBody(name=token, entity_token=token, face_count=face_count)
    if kind == "face":
        return body, [BRepFace(None, body=entity_proxy(body)) for _ in range(count)]
    edges = []
    for _ in range(count):
        e = type("E", (), {})()
        e.body = entity_proxy(body)
        edges.append(e)
    return body, edges


class TestOwningBodies:
    def test_many_faces_of_one_body_yield_exactly_one_body(self):
        # THE load-bearing pin for model_split and surface_delete_face: face.body hands back a fresh
        # proxy per read, so three faces of one body are three distinct Python objects sharing one
        # entityToken. Keying on identity yields 3 and inflates every per-body sum built on it.
        _body, faces = _entities_on_one_body(3)
        assert len(geom.owning_bodies(faces)) == 1

    def test_many_edges_of_one_body_yield_exactly_one_body(self):
        # edge.body behaves the same way - this is what EdgeLoopRef's body_count counts through.
        _body, edges = _entities_on_one_body(3, kind="edge")
        assert len(geom.owning_bodies(edges)) == 1

    def test_distinct_bodies_are_kept_apart(self):
        _b1, f1 = _entities_on_one_body(2, token="BodyA")
        _b2, f2 = _entities_on_one_body(2, token="BodyB")
        assert len(geom.owning_bodies(f1 + f2)) == 2

    def test_first_seen_order_is_preserved(self):
        _b1, f1 = _entities_on_one_body(1, token="BodyA")
        _b2, f2 = _entities_on_one_body(1, token="BodyB")
        tokens = [b.entityToken for b in geom.owning_bodies(f2 + f1)]
        assert tokens == ["BodyB", "BodyA"]

    def test_entity_with_no_readable_body_is_skipped(self):
        assert geom.owning_bodies([type("F", (), {})()]) == []

    def test_untokened_bodies_fall_back_to_identity(self):
        # The `or id(b)` last resort: without a token there is nothing else to key on, so two
        # separate proxies of one body read as two. Pinned so the fallback's LIMIT is visible - a
        # live entityToken read is measured non-empty, so this is not the normal path.
        body = BRepBody(name="NoToken", face_count=4)
        body.entityToken = None
        faces = [BRepFace(None, body=entity_proxy(body)) for _ in range(2)]
        assert len(geom.owning_bodies(faces)) == 2


class TestVolumeAndFaceSamples:
    def test_face_count_delta_sums_each_body_once(self):
        body, faces = _entities_on_one_body(3, face_count=6)
        bodies = geom.owning_bodies(faces)
        before = geom.face_counts(bodies)
        body.faces._items.extend([None] * 3)          # the split adds 3 faces to the ONE body
        delta, readable = geom.face_count_delta(bodies, before)
        assert readable is True and delta == 3        # not 9

    def test_face_count_delta_reports_unreadable_rather_than_zero(self):
        body, faces = _entities_on_one_body(1)
        bodies = geom.owning_bodies(faces)
        before = geom.face_counts(bodies)
        del body.faces
        delta, readable = geom.face_count_delta(bodies, before)
        assert readable is False and delta == 0       # a real zero is distinguishable from this

    def test_volume_delta_sums_each_body_once(self):
        body, faces = _entities_on_one_body(2)
        body.volume = 100.0
        bodies = geom.owning_bodies(faces)
        before = geom.volumes(bodies)
        body.volume = 118.0
        delta, readable = geom.volume_delta(bodies, before)
        assert readable is True and delta == 18.0     # not 36.0


class TestSignedVolume:
    """The single-read counterpart to volumes(): mesh_shell/mesh_repair judge a hollowing on this
    number's SIGN, so anything that is not a real number must read as None (unknown), never 0.0."""

    def _body(self, volume):
        b = BRepBody(name="Mesh1", entity_token="TOK")
        b.volume = volume
        return b

    def test_reads_a_positive_volume_through(self):
        assert geom.signed_volume(self._body(12.5)) == 12.5

    def test_a_negative_volume_keeps_its_sign(self):
        # A mesh whose normals were reversed reports the same magnitude with the opposite sign -
        # dropping the sign (abs) is exactly what would make a reversed mesh read as solid.
        assert geom.signed_volume(self._body(-12.5)) == -12.5

    def test_a_non_numeric_volume_is_unknown_not_zero(self):
        # adsk mocks (and an unmodeled live property) hand back a truthy object for anything
        # unmodeled. Letting one through would make `volume < 0` a TypeError, or worse, compare
        # a Mock as if it were a measurement.
        from unittest.mock import Mock
        assert geom.signed_volume(self._body(Mock())) is None

    def test_a_boolean_volume_is_unknown(self):
        # bool is an int subclass, so a plain isinstance(v, (int, float)) would accept True as 1.0.
        assert geom.signed_volume(self._body(True)) is None

    def test_an_unreadable_volume_is_none_not_zero(self):
        class _Body:
            @property
            def volume(self):
                raise RuntimeError("volume unreadable")
        assert geom.signed_volume(_Body()) is None

    def test_a_genuine_zero_is_still_a_reading(self):
        # 0.0 is an ANSWER (an empty/degenerate body), distinguishable from the None above.
        assert geom.signed_volume(self._body(0.0)) == 0.0


# ── lump_count: the DISCONNECTED-piece read a join is verified with ──────────────────────────────

class _Lumps:
    def __init__(self, count):
        self.count = count


class TestLumpCount:
    def test_reads_the_bodys_lump_count(self):
        body = BRepBody(name="Weldment")
        body.lumps = _Lumps(3)
        assert geom.lump_count(body) == 3

    def test_a_fused_single_piece_body_reads_one(self):
        # 1 is the ANSWER a real fuse gives - it must not collapse to None/0, or the join warning
        # could never tell a fused result from an unreadable one.
        body = BRepBody(name="Bracket")
        body.lumps = _Lumps(1)
        assert geom.lump_count(body) == 1

    def test_a_body_without_lumps_is_unknown_not_zero(self):
        # A MeshBody carries no 'lumps' at all (its API surface has no counterpart to BRepBody.lumps),
        # so the read is UNKNOWN. Answering 0 or 1 here would let a mesh join claim it verified
        # something it cannot see.
        assert geom.lump_count(BRepBody(name="Scan")) is None

    def test_an_unreadable_count_is_none(self):
        class _Body:
            @property
            def lumps(self):
                raise RuntimeError("lumps unreadable")
        assert geom.lump_count(_Body()) is None

    def test_a_non_numeric_count_is_none(self):
        # adsk mocks hand back a truthy child object for anything unmodeled; letting one through
        # would make `result_lumps > 1` a TypeError at the warning site.
        from unittest.mock import Mock
        body = BRepBody(name="Mocked")
        body.lumps = Mock()
        assert geom.lump_count(body) is None

    def test_only_the_brep_body_carries_lumps_in_the_api_surface(self):
        # The split this helper's contract rests on, and the reason mesh_combine reaches for AABBs
        # instead: BRepBody exposes 'lumps', MeshBody exposes no counterpart. If a Fusion build ever
        # gives MeshBody a lump/shell count, this goes red and the mesh gap can close.
        import api_surface
        assert "lumps" in api_surface.PROPERTIES["fusion.BRepBody"]
        mesh_members = api_surface.PROPERTIES["fusion.MeshBody"]
        assert "lumps" not in mesh_members and "shells" not in mesh_members


# ── aabb_gap: the not-touching proof for a body kind carrying no lump count ──────────────────────

def _boxed(name, minp, maxp):
    return BRepBody(name=name, bbox=FakeBoundingBox3D(FakePoint(*minp), FakePoint(*maxp)))


class TestAabbGap:
    def test_boxes_apart_report_the_gap(self):
        # 4.4 cm of clear air on x: the two bodies CANNOT touch, whatever else is true of them.
        a = _boxed("Tensioner", (0, 0, 0), (1, 1, 1))
        b = _boxed("Stub", (5.4, 0, 0), (6.4, 1, 1))
        assert round(geom.aabb_gap(a, b), 6) == 4.4

    def test_the_gap_is_symmetric(self):
        a = _boxed("A", (0, 0, 0), (1, 1, 1))
        b = _boxed("B", (5.4, 0, 0), (6.4, 1, 1))
        assert geom.aabb_gap(a, b) == geom.aabb_gap(b, a)

    def test_a_separating_axis_wins_over_overlapping_ones(self):
        # Fully overlapping in x and z, 2 cm apart in y - one separating axis is enough to prove the
        # bodies are clear of each other, so the MAX (not the min) over the axes is the answer.
        a = _boxed("A", (0, 0, 0), (10, 1, 10))
        b = _boxed("B", (0, 3, 0), (10, 4, 10))
        assert geom.aabb_gap(a, b) == 2

    def test_overlapping_boxes_report_no_gap(self):
        # Overlap proves nothing about contact, so the value must be <= 0 and never trigger a
        # not-touching claim.
        a = _boxed("A", (0, 0, 0), (2, 2, 2))
        b = _boxed("B", (1, 1, 1), (3, 3, 3))
        assert geom.aabb_gap(a, b) == -1

    def test_touching_boxes_report_zero(self):
        # Face-to-face contact: gap 0, which is NOT a positive gap - a real fuse must not be warned on.
        a = _boxed("A", (0, 0, 0), (1, 1, 1))
        b = _boxed("B", (1, 0, 0), (2, 1, 1))
        assert geom.aabb_gap(a, b) == 0

    def test_an_unreadable_box_is_none(self):
        a = _boxed("A", (0, 0, 0), (1, 1, 1))
        assert geom.aabb_gap(a, BRepBody(name="NoBox")) is None
        assert geom.aabb_gap(BRepBody(name="NoBox"), a) is None

    def test_a_non_numeric_coordinate_is_none(self):
        from unittest.mock import Mock
        a = _boxed("A", (0, 0, 0), (1, 1, 1))
        b = BRepBody(name="B", bbox=FakeBoundingBox3D(FakePoint(Mock(), 0, 0), FakePoint(1, 1, 1)))
        assert geom.aabb_gap(a, b) is None


class TestAabbGapSameSpacePrecondition:
    """The boxes only subtract if they are expressed in ONE space. An occurrence PROXY's box is in
    ROOT space while its native's is component-LOCAL, so a cross-wrapper subtraction mints a
    confident number out of two different frames - under the word PROVES, that is a fabrication."""

    def _in_comp(self, name, comp, minp, maxp):
        return BRepBody(name=name, parent_component=comp,
                        bbox=FakeBoundingBox3D(FakePoint(*minp), FakePoint(*maxp)))

    def test_a_native_and_an_occurrence_proxy_do_not_compare(self):
        import types
        from conftest import body_proxy
        comp = types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame")
        native = self._in_comp("Native", comp, (0, 0, 0), (1, 1, 1))
        proxy = body_proxy(native, types.SimpleNamespace(name="Frame:1", fullPathName="Frame:1"))
        other = self._in_comp("Other", comp, (5, 0, 0), (6, 1, 1))
        # the proxy really is the hard case: it answers an assemblyContext, the native does not
        assert proxy.assemblyContext is not None and native.assemblyContext is None
        assert geom.aabb_gap(proxy, other) is None

    def test_two_bodies_of_the_same_component_compare(self):
        import types
        comp = types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame")
        a = self._in_comp("A", comp, (0, 0, 0), (1, 1, 1))
        b = self._in_comp("B", comp, (5, 0, 0), (6, 1, 1))
        assert geom.aabb_gap(a, b) == 4

    def test_two_wrappers_of_one_component_still_compare(self):
        # Component wrappers are never identity-stable: two reads of one component are different
        # objects sharing a token. An identity test here would refuse every legitimate pair.
        import types
        comp_a = types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame")
        comp_b = types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame")
        assert comp_a is not comp_b
        a = self._in_comp("A", comp_a, (0, 0, 0), (1, 1, 1))
        b = self._in_comp("B", comp_b, (5, 0, 0), (6, 1, 1))
        assert geom.aabb_gap(a, b) == 4

    def test_bodies_of_different_components_do_not_compare(self):
        # Two component-LOCAL boxes from different components are in different frames; subtracting
        # them reports a gap that describes neither.
        import types
        a = self._in_comp("A", types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame"),
                          (0, 0, 0), (1, 1, 1))
        b = self._in_comp("B", types.SimpleNamespace(name="Lid", entityToken="CTOK::Lid"),
                          (5, 0, 0), (6, 1, 1))
        assert geom.aabb_gap(a, b) is None

    def test_proxies_under_different_occurrences_do_not_compare(self):
        # Two proxies whose assembly contexts sit on DIFFERENT components are in different frames
        # even though both answer an assemblyContext.
        import types
        from conftest import body_proxy
        comp_a = types.SimpleNamespace(name="Frame", entityToken="CTOK::Frame")
        comp_b = types.SimpleNamespace(name="Lid", entityToken="CTOK::Lid")
        pa = body_proxy(self._in_comp("A", comp_a, (0, 0, 0), (1, 1, 1)),
                        types.SimpleNamespace(name="Frame:1", fullPathName="Frame:1",
                                              component=comp_a))
        pb = body_proxy(self._in_comp("B", comp_b, (5, 0, 0), (6, 1, 1)),
                        types.SimpleNamespace(name="Lid:1", fullPathName="Lid:1",
                                              component=comp_b))
        assert geom.aabb_gap(pa, pb) is None


class _RaisingCoord:
    """A vector/point whose .y read raises - the unreadable-coordinate case the None-guards catch."""

    x = 1.0
    z = 0.0

    @property
    def y(self):
        raise RuntimeError("unreadable")


class TestUnreadableCoordinateGuards:
    def test_unit_vector_with_an_unreadable_coordinate_is_none(self):
        assert geom.unit_vector(_RaisingCoord()) is None

    def test_unit_vector_between_with_an_unreadable_endpoint_is_none(self):
        assert geom.unit_vector_between(_RaisingCoord(), FakePoint(1, 0, 0)) is None
        assert geom.unit_vector_between(FakePoint(0, 0, 0), _RaisingCoord()) is None

    def test_axis_vec_with_a_non_numeric_component_is_none(self):
        from unittest.mock import Mock
        assert geom.axis_vec(FakeVector3D(Mock(), 0, 0)) is None
        assert geom.axis_vec(_RaisingCoord()) is None


class TestOccWorldFrameGuards:
    """occ_world_frame omits keys rather than faking them: every unreadable piece drops ONLY its
    own keys, so a partial read stays honest instead of publishing a zeroed placement."""

    def test_an_unreadable_transform_omits_origin_and_axes_but_keeps_the_bbox(self):
        import types
        # body_aabb reads Occurrence.boundingBox2(entityTypes) - the bodies-only box.
        box = FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(2, 2, 2))
        occ = types.SimpleNamespace(transform2=None, boundingBox2=lambda types_: box)
        out = geom.occ_world_frame(occ, 10.0)
        assert "origin" not in out and "x_axis" not in out
        assert out["bbox_center"] == [10.0, 10.0, 10.0]
        assert out["bbox_size"] == [20.0, 20.0, 20.0]

    def test_a_malformed_coordinate_system_omits_the_axes(self):
        import types
        m = types.SimpleNamespace(translation=FakeVector3D(1, 2, 3),
                                  getAsCoordinateSystem=lambda: "not-a-4-tuple")
        occ = types.SimpleNamespace(transform2=m, bRepBodies=[])
        out = geom.occ_world_frame(occ, 10.0)
        assert out["origin"] == [10.0, 20.0, 30.0]
        assert "x_axis" not in out and "y_axis" not in out and "z_axis" not in out

    def test_a_degenerate_axis_is_omitted_while_readable_ones_land(self):
        import types
        from unittest.mock import Mock
        cs = (FakePoint(0, 0, 0), FakeVector3D(1, 0, 0), FakeVector3D(Mock(), 0, 0),
              FakeVector3D(0, 0, 1))
        m = types.SimpleNamespace(translation=FakeVector3D(0, 0, 0),
                                  getAsCoordinateSystem=lambda: cs)
        occ = types.SimpleNamespace(transform2=m, bRepBodies=[])
        out = geom.occ_world_frame(occ, 1.0)
        assert out["x_axis"] == [1.0, 0.0, 0.0] and out["z_axis"] == [0.0, 0.0, 1.0]
        assert "y_axis" not in out

    def test_a_translation_component_that_will_not_read_omits_the_origin(self):
        # 0.0 for the component that failed places the part AT the world origin as a measured
        # position - the same fabrication axis_vec refuses for a direction, on the number a caller
        # positions and measures against.
        import types
        m = types.SimpleNamespace(translation=_RaisingCoord(), getAsCoordinateSystem=lambda: None)
        occ = types.SimpleNamespace(transform2=m, bRepBodies=[])
        assert "origin" not in geom.occ_world_frame(occ, 1.0)

    def test_a_non_numeric_translation_component_omits_the_origin(self):
        import types
        from unittest.mock import Mock
        m = types.SimpleNamespace(translation=FakeVector3D(Mock(), 0, 0),
                                  getAsCoordinateSystem=lambda: None)
        occ = types.SimpleNamespace(transform2=m, bRepBodies=[])
        assert "origin" not in geom.occ_world_frame(occ, 1.0)

    def test_an_occurrence_really_at_the_world_origin_publishes_zeros(self):
        # 0,0,0 is an ANSWER (an unmoved occurrence), distinguishable from the omissions above
        import types
        m = types.SimpleNamespace(translation=FakeVector3D(0, 0, 0),
                                  getAsCoordinateSystem=lambda: None)
        occ = types.SimpleNamespace(transform2=m, bRepBodies=[])
        assert geom.occ_world_frame(occ, 10.0)["origin"] == [0.0, 0.0, 0.0]

    def test_an_unreadable_bbox_endpoint_omits_the_bbox_keys(self):
        import types

        class _NoMin:
            minPoint = property(lambda s: (_ for _ in ()).throw(RuntimeError("unreadable")))
            maxPoint = FakePoint(1, 1, 1)

        occ = types.SimpleNamespace(transform2=None, boundingBox2=lambda types_: _NoMin())
        out = geom.occ_world_frame(occ, 1.0)
        assert "bbox_center" not in out and "bbox_size" not in out

    def test_a_corner_COORDINATE_that_will_not_read_omits_the_bbox_keys(self):
        # The box and both corner POINTS read; one corner's .y does not. The centre/size arithmetic
        # is a guarded read like every other coordinate here, so the two keys drop - unguarded, the
        # raise leaves the occurrence row's caller with no row at all.
        import types
        box = FakeBoundingBox3D(_RaisingCoord(), FakePoint(2, 2, 2))
        occ = types.SimpleNamespace(transform2=None, boundingBox2=lambda types_: box)
        out = geom.occ_world_frame(occ, 1.0)
        assert "bbox_center" not in out and "bbox_size" not in out

    def test_a_non_numeric_corner_coordinate_omits_the_bbox_keys(self):
        # adsk mocks answer a truthy Mock for anything unmodeled; multiplying one into bbox_size
        # would publish a Mock as a measurement (or raise at json time).
        import types
        from unittest.mock import Mock
        box = FakeBoundingBox3D(FakePoint(0, Mock(), 0), FakePoint(2, 2, 2))
        occ = types.SimpleNamespace(transform2=None, boundingBox2=lambda types_: box)
        assert geom.occ_world_frame(occ, 1.0) == {}

    def test_a_fully_readable_box_still_reports_centre_and_size(self):
        # The guard must not cost the normal reading: an occurrence spanning (0,0,0)-(2,4,6) cm in
        # mm reads centre (10,20,30) and size (20,40,60).
        import types
        box = FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(2, 4, 6))
        occ = types.SimpleNamespace(transform2=None, boundingBox2=lambda types_: box)
        out = geom.occ_world_frame(occ, 10.0)
        assert out["bbox_center"] == [10.0, 20.0, 30.0]
        assert out["bbox_size"] == [20.0, 40.0, 60.0]
