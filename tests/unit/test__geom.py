"""Unit tests for ``_geom.py`` - the direction-vector math find_geometry and sys_get_selection
share: normalizing a Vector3D (``unit_vector``), the unit direction between two points
(``unit_vector_between``), and a face's evaluator-sampled normal (``evaluator_normal_at``).
"""

import math

from conftest import FakePoint, FakeVector3D, load_tool

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
