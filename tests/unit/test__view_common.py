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
from conftest import FakePoint, MakeComp, load_tool, make_source_document

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


class TestIsoCornersMirrorAcrossZ:
    """Fusion is Z-up, so an iso-BOTTOM view must put the eye BELOW the model. A positive eye z on an
    iso-bottom-* entry aims the camera down at the TOP face - the same image its iso-top twin gives,
    which makes the two names indistinguishable and a visual bottom-side check worthless."""

    @pytest.mark.parametrize("name", ("iso-bottom-right", "iso-bottom-left"))
    def test_iso_bottom_eye_is_below_the_model(self, name):
        assert vc.view_direction(name)[2] < 0, name

    @pytest.mark.parametrize("name", ("iso-top-right", "iso-top-left"))
    def test_iso_top_eye_is_above_the_model(self, name):
        assert vc.view_direction(name)[2] > 0, name

    @pytest.mark.parametrize("top,bottom", [("iso-top-right", "iso-bottom-right"),
                                            ("iso-top-left", "iso-bottom-left")])
    def test_each_iso_bottom_mirrors_its_top_twin_across_z(self, top, bottom):
        tx, ty, tz = vc.view_direction(top)
        assert vc.view_direction(bottom) == (tx, ty, -tz)

    @pytest.mark.parametrize("right,left", [("iso-top-right", "iso-top-left"),
                                            ("iso-bottom-right", "iso-bottom-left")])
    def test_the_right_and_left_corner_of_a_pair_differ_in_x_only(self, right, left):
        # the -right/-left half of the name is the eye's x sign; agreeing on x would make the two
        # names render the same image, the same way the z sign collapsed top onto bottom.
        rx, ry, rz = vc.view_direction(right)
        assert vc.view_direction(left) == (-rx, ry, rz)

    def test_the_four_iso_corners_are_four_distinct_directions(self):
        corners = {vc.view_direction(n) for n in
                   ("iso-top-right", "iso-top-left", "iso-bottom-right", "iso-bottom-left")}
        assert len(corners) == 4


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

class _FakeCam:
    def __init__(self):
        # conftest's shared point: apply_named_view reads eye.distanceTo(target) for the standoff
        self.eye = FakePoint(5, 0, 0)
        self.target = FakePoint(0, 0, 0)
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

    def test_the_standoff_survives_the_orient(self, monkeypatch):
        # The orient re-places the eye along the named view's look direction and must leave it the
        # SAME distance from the target it started at: apply_named_view reads that distance off the
        # pre-orient camera, then rebuilds the eye from the TARGET. The expected distance is
        # computed HERE from the coordinates, never through the same eye.distanceTo the orient
        # reads, so a distanceTo answering a constant fails this instead of agreeing with itself.
        # An iso corner is used because its table entry is un-normalized - iso-top-right is
        # (1, -1, 1), of length sqrt(3) - so the assertion covers the normalization too.
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        cam = _FakeCam()
        cam.target = FakePoint(2, -3, 4)         # a focus off the origin
        cam.eye = FakePoint(5, 1, 16)            # (3, 4, 12) from it - a standoff of exactly 13
        vp = _FakeViewport(cam=cam)
        vc.apply_named_view(vp, "iso-top-right")
        eye, tgt = vp._cam.eye, vp._cam.target
        assert math.isclose(math.dist((eye.x, eye.y, eye.z), (tgt.x, tgt.y, tgt.z)), 13.0,
                            rel_tol=1e-9)

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


# The x-ref shape for COMPONENTS, measured on a CAM job assembled from 7 source documents: each
# document's ROOT component reads the SAME byte-identical entityToken while the documents' lineage
# ids differ. A token-only key collapses all of them onto one entry.
_ROOT_TOKEN = "/v4BAAEAAwAAAAAAAAAAAAAA"
_JOB_URNS = ("urn:adsk.wipprod:dm.lineage:K3I2nkywRlaWPHJexysOdA",
             "urn:adsk.wipprod:dm.lineage:N_QoPrrrSJmF__f9BZV86A",
             "urn:adsk.wipprod:dm.lineage:Qb7yTHkCTVSp6t9V9YQKuw")


def _root_of_document(name, urn, token=_ROOT_TOKEN):
    """One document's ROOT component: its own entityToken plus the parentDesign hop a component's
    source document is read through (parentDesign -> parentDocument -> dataFile.id)."""
    return MakeComp(name=name, entity_token=token, parent_design=make_source_document(urn))


class TestAllDisplayComponents:
    """The deduped component walk every folder-bulb toggle runs. The key is the physical-entity
    identity: allComponents holds a root proxy DISTINCT from rootComponent (so Python identity would
    toggle the root twice), while an entityToken is DOCUMENT-LOCAL and shared by every document's
    root (so a token-only key drops every root but one)."""

    def test_roots_of_several_source_documents_are_all_walked(self):
        # Keyed on the bare token these DISTINCT components collapse to one entry and the folder
        # bulbs of the others are never written at all.
        roots = [_root_of_document(f"Doc{i}", urn) for i, urn in enumerate(_JOB_URNS)]
        assert len({c.entityToken for c in roots}) == 1      # the tokens really collide
        design = SimpleNamespace(rootComponent=roots[0], allComponents=roots)
        assert vc.all_display_components(design) == roots

    def test_a_shared_token_inside_ONE_document_still_collapses(self):
        # The other direction: the document half must not split a component from its own proxy, or
        # the root's bulbs would be written twice on every design.
        root = _root_of_document("Doc0", _JOB_URNS[0])
        proxy = _root_of_document("Doc0", _JOB_URNS[0])
        design = SimpleNamespace(rootComponent=root, allComponents=[proxy])
        assert vc.all_display_components(design) == [root]

    def test_a_component_whose_document_reads_is_kept_apart_from_one_whose_does_not(self):
        # A source document that will not read answers None, which is a DIFFERENT urn half from a
        # real lineage id - so the component nothing could be read from is never folded into a
        # component that was identified.
        placed = _root_of_document("Doc0", _JOB_URNS[0])
        loose = SimpleNamespace(name="Loose", entityToken=_ROOT_TOKEN)   # no parentDesign at all
        design = SimpleNamespace(rootComponent=placed, allComponents=[loose])
        assert vc.all_display_components(design) == [placed, loose]

    def test_root_proxy_in_allcomponents_is_collapsed(self):
        from types import SimpleNamespace
        root = SimpleNamespace(name="Root", entityToken="tok-root")
        root_proxy = SimpleNamespace(name="Root", entityToken="tok-root")
        sub = SimpleNamespace(name="Sub", entityToken="tok-sub")
        design = SimpleNamespace(rootComponent=root, allComponents=[root_proxy, sub])
        comps = vc.all_display_components(design)
        assert comps == [root, sub]                       # the proxy never doubles the root

    def test_tokenless_components_fall_back_to_identity(self):
        from types import SimpleNamespace
        a = SimpleNamespace(name="A")
        b = SimpleNamespace(name="B")
        design = SimpleNamespace(rootComponent=a, allComponents=[a, b])
        comps = vc.all_display_components(design)
        assert comps == [a, b]                            # same OBJECT deduped; distinct kept

    def test_an_unreadable_allcomponents_still_yields_the_root(self):
        from types import SimpleNamespace
        root = SimpleNamespace(name="Root", entityToken="tok-root")
        design = SimpleNamespace(rootComponent=root)      # no allComponents at all
        assert vc.all_display_components(design) == [root]

    def test_display_folders_name_the_four_component_folder_bulbs(self):
        assert vc.DISPLAY_FOLDERS == {
            "sketches": "isSketchFolderLightBulbOn",
            "construction": "isConstructionFolderLightBulbOn",
            "origins": "isOriginFolderLightBulbOn",
            "joints": "isJointsFolderLightBulbOn",
        }


class TestIsolateForFit:
    """The frame-on-one-occurrence walk view_screenshot's fit_to and view_set's focus= both fit
    through. Its errors are worded by the CALLER's own input kind, so neither tool reports a
    refusal naming the other one's parameter."""

    def test_no_active_design_names_the_callers_own_input(self, monkeypatch):
        monkeypatch.setattr(vc._common, "design", lambda: None)
        ref = SimpleNamespace(name="focus")
        restore, target, err = vc.isolate_for_fit("Part:1", ref)
        assert restore is None and target is None
        assert err.startswith("focus:") and "Part:1" in err

    def test_a_display_folder_that_will_not_relight_is_named(self, monkeypatch):
        class Comp:
            """A component whose sketch folder accepts the hide and refuses to come back on."""

            def __init__(self):
                self.name = "Blocky"
                self.entityToken = "tok"
                self.isSketchFolderLightBulbOn = True
                self.isConstructionFolderLightBulbOn = False
                self.isOriginFolderLightBulbOn = False
                self.isJointsFolderLightBulbOn = False

            def __setattr__(self, key, value):
                if key == "isSketchFolderLightBulbOn" and value is True                         and getattr(self, "_darkened", False):
                    return
                if key == "isSketchFolderLightBulbOn" and value is False:
                    object.__setattr__(self, "_darkened", True)
                object.__setattr__(self, key, value)

        comp = Comp()
        occ = SimpleNamespace(fullPathName="Part:1", name="Part:1", isLightBulbOn=True)
        design = SimpleNamespace(rootComponent=comp, allComponents=[comp])
        monkeypatch.setattr(vc._common, "design", lambda: design)
        monkeypatch.setattr(vc._common, "all_occurrences", lambda d: [occ])
        ref = SimpleNamespace(name="fit_to", resolve=lambda raw: (occ, None))
        restore, target, err = vc.isolate_for_fit("Part:1", ref)
        assert err is None and target is occ
        assert comp.isSketchFolderLightBulbOn is False    # the fit really did clear the clutter
        # the folder stays dark, and the caller is told WHICH one - not left with a silent change
        assert restore() == ["Blocky:isSketchFolderLightBulbOn"]


class TestRestoreMessage:
    def test_a_clean_restore_says_nothing(self):
        assert vc.restore_message(lambda: [], "fit_to", "for this shot") is None

    def test_no_restore_at_all_says_nothing(self):
        assert vc.restore_message(None, "fit_to", "for this shot") is None

    def test_the_message_carries_the_callers_label_and_purpose(self):
        msg = vc.restore_message(lambda: ["A:1", "B:1"], "'focus'", "to frame the view")
        assert msg.startswith("'focus' hid the other occurrences to frame the view")
        assert "2 of them" in msg and "A:1" in msg and "B:1" in msg

    def test_a_raising_restore_is_reported_not_swallowed(self):
        def boom():
            raise RuntimeError("bulb bus offline")

        msg = vc.restore_message(boom, "fit_to", "for this shot")
        assert "bulb bus offline" in msg

    def test_only_the_first_five_stuck_names_are_listed(self):
        msg = vc.restore_message(lambda: [f"O{i}:1" for i in range(9)], "fit_to", "for this shot")
        assert "9 of them" in msg                       # the COUNT is complete...
        assert "O4:1" in msg and "O5:1" not in msg      # ...while the listing stays bounded

