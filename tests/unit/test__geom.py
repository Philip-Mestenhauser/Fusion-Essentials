"""Unit tests for ``_geom.py`` - the direction-vector math find_geometry and sys_get_selection
share: normalizing a Vector3D (``unit_vector``), the unit direction between two points
(``unit_vector_between``), and a face's evaluator-sampled normal (``evaluator_normal_at``); plus the
owning-body walk and the before/after volume and face-count samples every effect check reads through
(``owning_bodies`` / ``volumes`` / ``volume_delta`` / ``face_counts`` / ``face_count_delta``).
"""

import math

from conftest import (BRepBody, BRepFace, FakePoint, FakeVector3D, entity_proxy, load_tool)

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
