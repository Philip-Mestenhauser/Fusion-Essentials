"""Unit tests for ``_view_common.py`` - the shared camera-orientation table for the standard
named views. view_screenshot and view_set must produce the SAME camera for a given named
view even though they consume opposite sign conventions (view_screenshot's look_direction is the
negation of view_set's view_direction); this pins that relationship so the two can't silently
desync.
"""

import math

from conftest import load_tool

vc = load_tool("_view_common")

_NAMED_VIEWS = ("front", "back", "top", "bottom", "right", "left",
                "iso-top-right", "iso-top-left", "iso-bottom-right", "iso-bottom-left")


def _unit(v):
    return math.isclose(sum(c * c for c in v) ** 0.5, 1.0, abs_tol=1e-9)


class TestViewDirection:
    def test_known_views_return_unit_vectors(self):
        for name in _NAMED_VIEWS:
            assert _unit(vc.view_direction(name)), name

    def test_unknown_view_returns_none(self):
        assert vc.view_direction("banana") is None
        assert vc.view_direction("current") is None

    def test_front_points_toward_minus_y(self):
        assert vc.view_direction("front") == (0.0, -1.0, 0.0)

    def test_top_points_toward_plus_z(self):
        assert vc.view_direction("top") == (0.0, 0.0, 1.0)

    def test_right_points_toward_plus_x(self):
        assert vc.view_direction("right") == (1.0, 0.0, 0.0)


class TestLookDirection:
    def test_unknown_view_returns_none(self):
        assert vc.look_direction("banana") is None

    def test_front_looks_along_plus_y(self):
        assert vc.look_direction("front") == (0.0, 1.0, 0.0)

    def test_known_views_return_unit_vectors(self):
        for name in _NAMED_VIEWS:
            assert _unit(vc.look_direction(name)), name


class TestSignRelationship:
    """The relationship view_screenshot and view_set both rely on: for every named view,
    view_screenshot's applied look-direction is the exact negation of view_set's applied
    view-direction. Pinned here so a future edit to either side can't silently desync the two."""

    def test_look_direction_is_negated_view_direction_for_every_named_view(self):
        for name in _NAMED_VIEWS:
            look = vc.look_direction(name)
            view = vc.view_direction(name)
            assert look == tuple(-c for c in view), name


class TestUpVector:
    def test_unknown_view_returns_none(self):
        assert vc.up_vector("banana") is None

    def test_top_and_bottom_use_y_up(self):
        assert vc.up_vector("top") == (0, 1, 0)
        assert vc.up_vector("bottom") == (0, 1, 0)

    def test_faces_and_isos_otherwise_use_z_up(self):
        for name in ("front", "back", "right", "left",
                     "iso-top-right", "iso-top-left", "iso-bottom-right", "iso-bottom-left"):
            assert vc.up_vector(name) == (0, 0, 1), name

    def test_up_vector_is_the_same_for_both_conventions(self):
        # up does not flip sign between view_direction and look_direction consumers.
        for name in _NAMED_VIEWS:
            assert vc.up_vector(name) is not None


class TestOrthoFace:
    def test_six_true_faces_are_ortho(self):
        for name in ("front", "back", "top", "bottom", "right", "left"):
            assert vc.is_ortho_face(name) is True

    def test_iso_corners_and_unknown_are_not_ortho(self):
        for name in ("iso-top-right", "iso-top-left", "iso-bottom-right", "iso-bottom-left",
                     "current", "banana"):
            assert vc.is_ortho_face(name) is False
