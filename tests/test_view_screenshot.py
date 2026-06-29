"""Unit tests for ``get_screenshot.py`` _isolate_for_fit — the fit_to visibility helper.

The image capture itself needs a live viewport (integration-tested), but the fit_to helper is pure
visibility bookkeeping: find the named occurrence, hide the others, return a restore() that turns
them back on. That's exactly the bug-prone part (matching + restore), so it gets unit coverage.
"""

from conftest import load_tool

gs = load_tool("view_screenshot")


class FakeOcc:
    def __init__(self, name, on=True):
        self.name = name
        self.fullPathName = name
        self.isLightBulbOn = on


class FakeRoot:
    def __init__(self, occs):
        self.allOccurrences = occs


class FakeDesign:
    def __init__(self, occs):
        self.rootComponent = FakeRoot(occs)


def _install(occs):
    design = FakeDesign(occs)
    app = type("A", (), {"activeProduct": design})()
    gs.app = app
    # The occurrence resolver runs through the shared OccurrenceRef kind, which reads _common.design()
    # -> _common.app. Point that at the same fake app so resolution and the isolate logic agree.
    gs._inputs._common.app = app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design


class TestIsolateForFit:
    def test_hides_others_and_restores(self):
        a, b, c = FakeOcc("A:1"), FakeOcc("B:1"), FakeOcc("C:1")
        _install([a, b, c])
        restore = gs._isolate_for_fit("B:1")
        assert restore is not None
        # only B stays on
        assert b.isLightBulbOn is True
        assert a.isLightBulbOn is False and c.isLightBulbOn is False
        restore()
        assert a.isLightBulbOn is True and c.isLightBulbOn is True

    def test_substring_match(self):
        a = FakeOcc("Bracket:1")
        _install([a, FakeOcc("Other:1")])
        restore = gs._isolate_for_fit("bracket")
        assert restore is not None and a.isLightBulbOn is True

    def test_no_match_returns_none(self):
        _install([FakeOcc("A:1")])
        assert gs._isolate_for_fit("Ghost") is None

    def test_already_hidden_others_not_restored_on(self):
        # an occurrence that was already OFF should stay off after restore (we only flip ones we hid)
        a, b = FakeOcc("A:1", on=True), FakeOcc("B:1", on=False)
        _install([a, b])
        restore = gs._isolate_for_fit("A:1")
        restore()
        assert b.isLightBulbOn is False      # we never turned it on


# ── true-orthographic camera vectors (square 'right'/'top' views) ──
# The camera is set to EXACT world-axis vectors per named view, so an orthographic read is guaranteed
# square ([±1,0,0] etc.) — not a rotate-toward that leaves a tilt and silently distorts the screenshot.

class TestOrthoCameraVectors:
    def _unit(self, v):
        import math
        return math.isclose(sum(c * c for c in v) ** 0.5, 1.0, abs_tol=1e-9)

    def test_front_looks_along_plus_y_z_up(self):
        look, up = gs._ortho_camera_vectors("front")
        assert look == (0, 1, 0) and up == (0, 0, 1)

    def test_right_looks_along_minus_x(self):
        look, up = gs._ortho_camera_vectors("right")
        assert look == (-1, 0, 0) and up == (0, 0, 1)

    def test_top_looks_down_z(self):
        look, up = gs._ortho_camera_vectors("top")
        assert look == (0, 0, -1)

    def test_all_six_faces_are_pure_world_axes(self):
        # every orthographic FACE look-dir is a pure ±world-axis (exactly one nonzero == ±1)
        for v in ("front", "back", "top", "bottom", "right", "left"):
            look, _ = gs._ortho_camera_vectors(v)
            nonzero = [c for c in look if c != 0]
            assert len(nonzero) == 1 and abs(nonzero[0]) == 1, f"{v} look {look} not a pure axis"

    def test_iso_vectors_are_unit_length(self):
        for v in ("iso-top-right", "iso-top-left", "iso-bottom-right", "iso-bottom-left"):
            look, up = gs._ortho_camera_vectors(v)
            assert self._unit(look), f"{v} look not unit length"

    def test_current_and_unknown_return_none(self):
        assert gs._ortho_camera_vectors("current") is None
        assert gs._ortho_camera_vectors("banana") is None

    def test_only_the_six_faces_force_orthographic(self):
        for v in ("front", "back", "top", "bottom", "right", "left"):
            assert gs._is_ortho_face(v) is True
        for v in ("iso-top-right", "current", "banana"):
            assert gs._is_ortho_face(v) is False
