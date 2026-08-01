"""Unit tests for ``_view_common.py`` - the shared camera-orientation table for the standard
named views. view_screenshot and view_set must produce the SAME camera for a given named
view even though they consume opposite sign conventions (view_screenshot's look_direction is the
negation of view_set's view_direction); this pins that relationship so the two can't silently
desync.
"""

import math
from types import SimpleNamespace

import pytest

import live_api_facts
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


# ── apply_named_view: the one orient-then-fit both screenshot tools share ────

class _FakePoint:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def distanceTo(self, other):
        return 10.0


class _FakeCam:
    def __init__(self):
        self.eye = _FakePoint(5, 0, 0)
        self.target = _FakePoint(0, 0, 0)
        self.upVector = None
        self.cameraType = "initial"


def _fake_options(filename):
    """A SaveImageFileOptions stand-in at the initial values a freshly created options object
    carries - width/height 0, isBackgroundTransparent false, isAntiAliased true - measured live
    as BEHAVIOR['save_image_options_defaults'], which the tests below gate on."""
    return SimpleNamespace(filename=filename, width=0, height=0,
                           isBackgroundTransparent=False, isAntiAliased=True)


class _FakeViewport:
    def __init__(self, cam=None, save_ok=True, png=b"PNGBYTES"):
        self._cam = cam or _FakeCam()
        self.assigned_camera = None
        self.fit_called = 0
        self.calls = []
        self._save_ok = save_ok
        self._png = png
        self.options_used = None

    @property
    def camera(self):
        return self._cam

    @camera.setter
    def camera(self, value):
        self.assigned_camera = value

    def fit(self):
        self.fit_called += 1

    def refresh(self):
        self.calls.append("refresh")

    def saveAsImageFile(self, path, w, h):
        self.calls.append("save")
        if not self._save_ok:
            return False
        with open(path, "wb") as f:
            f.write(self._png)
        return True

    def saveAsImageFileWithOptions(self, options):
        self.calls.append("save_with_options")
        self.options_used = options
        if not self._save_ok:
            return False
        with open(options.filename, "wb") as f:
            f.write(self._png)
        return True


@pytest.fixture
def options_kind(monkeypatch):
    """SaveImageFileOptions.create -> the local stand-in, so the options capture path runs on real
    attribute writes instead of a Mock that swallows every assignment."""
    import adsk.core
    monkeypatch.setattr(adsk.core, "SaveImageFileOptions",
                        SimpleNamespace(create=_fake_options), raising=False)


class TestApplyNamedView:
    def test_named_view_assigns_camera_and_fits(self):
        vp = _FakeViewport()
        vc.apply_named_view(vp, "front")
        assert vp.assigned_camera is vp._cam     # assigning back applies the change
        assert vp.fit_called == 1

    def test_true_face_forces_orthographic_camera(self):
        vp = _FakeViewport()
        vc.apply_named_view(vp, "front")
        assert vp._cam.cameraType != "initial"

    def test_iso_corner_keeps_camera_type(self):
        vp = _FakeViewport()
        vc.apply_named_view(vp, "iso-top-right")
        assert vp._cam.cameraType == "initial"

    def test_unknown_or_current_is_a_noop(self):
        for name in ("current", "banana"):
            vp = _FakeViewport()
            vc.apply_named_view(vp, name)
            assert vp.assigned_camera is None and vp.fit_called == 0


class TestCapturePngB64:
    def test_refreshes_before_the_grab(self):
        # the refresh must precede saveAsImageFile - the capture otherwise races an un-refreshed
        # frame (a camera/visibility change that hasn't drawn yet reads as blank).
        vp = _FakeViewport()
        b64, err = vc.capture_png_b64(vp, 100, 80)
        assert err is None
        assert vp.calls.index("refresh") < vp.calls.index("save")

    def test_returns_the_png_as_base64(self):
        import base64
        vp = _FakeViewport(png=b"IMAGEDATA")
        b64, err = vc.capture_png_b64(vp, 100, 80)
        assert err is None and base64.b64decode(b64) == b"IMAGEDATA"

    def test_save_returning_false_is_an_error_not_a_blank_ok(self):
        vp = _FakeViewport(save_ok=False)
        b64, err = vc.capture_png_b64(vp, 100, 80)
        assert b64 is None and "capture failed" in err.lower()
        assert "saveAsImageFile returned false" in err       # the overload that actually answered

    def test_empty_file_is_an_error_not_an_empty_base64_ok(self):
        # mkstemp already created the file, so file-exists proves nothing - a success that wrote no
        # bytes must not come back as an ok with an empty image.
        vp = _FakeViewport(png=b"")
        b64, err = vc.capture_png_b64(vp, 100, 80)
        assert b64 is None and "0-byte" in err


class TestCaptureOptionsPath:
    """transparent_background/anti_aliased route the grab through SaveImageFileOptions +
    saveAsImageFileWithOptions; with neither given the plain overload stays untouched."""

    def test_neither_switch_uses_the_plain_overload(self, options_kind):
        vp = _FakeViewport()
        b64, err = vc.capture_png_b64(vp, 100, 80)
        assert err is None
        assert "save" in vp.calls and "save_with_options" not in vp.calls
        assert vp.options_used is None

    def test_transparent_background_switches_to_the_options_overload(self, options_kind):
        vp = _FakeViewport()
        b64, err = vc.capture_png_b64(vp, 100, 80, transparent_background=True)
        assert err is None
        assert "save_with_options" in vp.calls and "save" not in vp.calls
        assert vp.options_used.isBackgroundTransparent is True

    def test_anti_aliased_alone_switches_to_the_options_overload(self, options_kind):
        vp = _FakeViewport()
        vc.capture_png_b64(vp, 100, 80, anti_aliased=False)
        assert vp.options_used.isAntiAliased is False
        # the switch NOT given is left at the options object's own measured initial value
        assert live_api_facts.BEHAVIOR["save_image_options_defaults"]
        assert vp.options_used.isBackgroundTransparent is False

    def test_false_is_a_request_not_an_absence(self, options_kind):
        # transparent_background=False must still take the options path (False != unset), otherwise
        # an explicit "opaque, anti-aliased" request silently falls back to the plain capture.
        vp = _FakeViewport()
        vc.capture_png_b64(vp, 100, 80, transparent_background=False)
        assert "save_with_options" in vp.calls
        assert vp.options_used.isBackgroundTransparent is False

    def test_requested_size_is_assigned_onto_the_options(self, options_kind):
        # a fresh options object starts at width/height 0
        # (BEHAVIOR['save_image_options_defaults']), so the requested pixel size is only
        # honoured if the capture assigns it.
        assert live_api_facts.BEHAVIOR["save_image_options_defaults"]
        vp = _FakeViewport()
        vc.capture_png_b64(vp, 1024, 768, anti_aliased=True)
        assert (vp.options_used.width, vp.options_used.height) == (1024, 768)

    def test_options_path_still_refreshes_first(self, options_kind):
        vp = _FakeViewport()
        vc.capture_png_b64(vp, 100, 80, transparent_background=True)
        assert vp.calls.index("refresh") < vp.calls.index("save_with_options")

    def test_options_path_returns_the_png_as_base64(self, options_kind):
        import base64
        vp = _FakeViewport(png=b"TRANSPARENTPNG")
        b64, err = vc.capture_png_b64(vp, 100, 80, transparent_background=True)
        assert err is None and base64.b64decode(b64) == b"TRANSPARENTPNG"

    def test_options_failure_names_the_options_overload(self, options_kind):
        vp = _FakeViewport(save_ok=False)
        b64, err = vc.capture_png_b64(vp, 100, 80, transparent_background=True)
        assert b64 is None and "saveAsImageFileWithOptions returned false" in err
