"""Unit tests for ``view_screenshot.py`` _isolate_for_fit - the fit_to visibility helper.

The image capture itself needs a live viewport (integration-tested), but the fit_to helper is pure
visibility bookkeeping: find the named occurrence, hide the others, return a restore() that turns
them back on. That's exactly the bug-prone part (matching + restore), so it gets unit coverage.
"""

import base64
import os
from types import SimpleNamespace

import pytest

from conftest import load_tool

gs = load_tool("view_screenshot")


class FakeOcc:
    def __init__(self, name, on=True):
        self.name = name
        self.fullPathName = name
        self.isLightBulbOn = on
        # A real Occurrence always answers `component`; one whose read RAISES is an unresolved
        # external reference, which the shared census keeps out of the isolation walk.
        self.component = SimpleNamespace(name=name.split(":")[0])


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
        restore, target, err = gs._isolate_for_fit("B:1")
        assert restore is not None and err is None
        assert target is b                     # the resolved occurrence rides along
        # only B stays on
        assert b.isLightBulbOn is True
        assert a.isLightBulbOn is False and c.isLightBulbOn is False
        restore()
        assert a.isLightBulbOn is True and c.isLightBulbOn is True

    def test_display_folders_hidden_for_the_shot_and_restored(self):
        # vp.fit() frames every VISIBLE entity, so a big construction plane in the fitted
        # component blows the frame to the whole scene (measured) - the shot switches the
        # per-component display folders off and the restore puts back exactly what it moved.
        a = FakeOcc("A:1")
        design = _install([a])
        r = design.rootComponent
        r.entityToken = "root-tok"
        r.isSketchFolderLightBulbOn = True
        r.isConstructionFolderLightBulbOn = True
        r.isOriginFolderLightBulbOn = False              # already off - never touched
        r.isJointsFolderLightBulbOn = True
        restore, _target, err = gs._isolate_for_fit("A:1")
        assert err is None
        assert r.isSketchFolderLightBulbOn is False
        assert r.isConstructionFolderLightBulbOn is False
        assert r.isJointsFolderLightBulbOn is False
        assert r.isOriginFolderLightBulbOn is False      # was off, stays off
        assert restore() == []
        assert r.isSketchFolderLightBulbOn is True
        assert r.isConstructionFolderLightBulbOn is True
        assert r.isJointsFolderLightBulbOn is True
        assert r.isOriginFolderLightBulbOn is False      # not moved, not force-lit

    def test_substring_match(self):
        a = FakeOcc("Bracket:1")
        _install([a, FakeOcc("Other:1")])
        restore, _target, err = gs._isolate_for_fit("bracket")
        assert restore is not None and err is None and a.isLightBulbOn is True

    def test_no_match_returns_none(self):
        _install([FakeOcc("A:1")])
        restore, _target, err = gs._isolate_for_fit("Ghost")
        assert restore is None and err is not None

    def test_ambiguous_name_refused_not_first_match(self):
        # two instances share local name "Bolt:1" under different sub-assemblies - a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT silently isolate the first.
        a = FakeOcc("Bolt:1"); a.fullPathName = "Sub-A:1+Bolt:1"
        b = FakeOcc("Bolt:1"); b.fullPathName = "Sub-B:1+Bolt:1"
        _install([a, b])
        restore, _target, err = gs._isolate_for_fit("Bolt")
        assert restore is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def test_already_hidden_others_not_restored_on(self):
        # an occurrence that was already OFF should stay off after restore (we only flip ones we hid)
        a, b = FakeOcc("A:1", on=True), FakeOcc("B:1", on=False)
        _install([a, b])
        restore, _target, err = gs._isolate_for_fit("A:1")
        restore()
        assert b.isLightBulbOn is False      # we never turned it on

    def test_a_clean_restore_reports_nothing_stuck(self):
        a, b = FakeOcc("A:1"), FakeOcc("B:1")
        _install([a, b])
        restore, _target, _err = gs._isolate_for_fit("A:1")
        assert restore() == []

    def test_a_bulb_that_will_not_come_back_on_is_named_by_the_restore(self):
        # This tool MUTATES visibility to take its picture. A restore that silently failed leaves
        # a read tool having changed the document, so the failure has to be reportable.
        class OneWay(FakeOcc):
            """A bulb that switches OFF and then refuses to come back ON - so the hide takes and
            the restore silently does not, which is the only shape that leaves a read tool
            having changed the document."""

            def __init__(self, name):
                super().__init__(name)
                object.__setattr__(self, "_armed", True)

            def __setattr__(self, key, value):
                if key == "isLightBulbOn" and value is True and getattr(self, "_armed", False):
                    return
                object.__setattr__(self, key, value)

        stuck = OneWay("B:1")
        object.__setattr__(stuck, "fullPathName", "Sub:1+B:1")
        _install([FakeOcc("A:1"), stuck])
        restore, _target, _err = gs._isolate_for_fit("A:1")
        assert stuck.isLightBulbOn is False           # the hide DID take
        assert restore() == ["Sub:1+B:1"]


# ── active-component note: a non-root activation dims everything else to ghosts ──
# When a sub-component is activated, the screenshot looks washed-out/translucent. The note names the
# active component so the agent reads that as activation scope, not a lighting/appearance bug.

class TestActiveComponentNote:
    def _design(self, active_is_root=True, active_name="Gimbal:1"):
        # activeOccurrence is None exactly when the root is active (the API contract the note keys
        # on); a non-root activation exposes the activated OCCURRENCE. An identity test of
        # activeComponent against rootComponent can never be true live - each property access mints
        # a new proxy - so the note must never be derived from a component comparison.
        from types import SimpleNamespace
        occ = None if active_is_root else SimpleNamespace(name=active_name)
        return SimpleNamespace(activeOccurrence=occ)

    def test_root_active_no_note(self):
        assert gs._active_component_note(self._design(active_is_root=True)) is None

    def test_sub_component_active_warns_and_names_it(self):
        note = gs._active_component_note(self._design(active_is_root=False, active_name="Rotor:1"))
        assert note is not None
        assert "Rotor:1" in note
        assert "dimmed" in note or "translucent" in note
        assert "lighting" in note            # explicitly rules out the misdiagnosis

    def test_none_design_is_safe(self):
        assert gs._active_component_note(None) is None


# The exact-world-axis camera math the handler orients with is _view_common.apply_named_view,
# pinned in test__view_common.py.

# ── _keep_visible: fit_to isolate keeps the target + its ancestors + descendants visible, matched ──
# ── by fullPathName (NOT Python `is`, which never matches across fresh occurrence proxies and would ──
# ── hide the target itself -> a BLANK image). Nesting boundary is '+' per level. ──────────────────

class TestCaptureSwitchPassThrough:
    """transparent_background/anti_aliased reach the ONE shared capture seam unchanged, and stay
    absent (None) when the caller omits them - the plain-overload default the seam keys on."""

    @pytest.fixture
    def rig(self, monkeypatch):
        monkeypatch.setattr(gs, "app", SimpleNamespace(activeViewport=SimpleNamespace()))
        monkeypatch.setattr(gs._common, "design", lambda: None)
        calls = []

        def fake_capture(viewport, width, height, prefix="fe_mcp_shot",
                         transparent_background=None, anti_aliased=None):
            calls.append({"width": width, "height": height,
                          "transparent_background": transparent_background,
                          "anti_aliased": anti_aliased})
            return "B64DATA", None

        monkeypatch.setattr(gs._view_common, "capture_png_b64", fake_capture)
        return calls

    def test_omitting_both_leaves_the_capture_on_the_plain_path(self, rig):
        result = gs.handler()
        assert result["isError"] is False
        assert rig == [{"width": 800, "height": 600,
                        "transparent_background": None, "anti_aliased": None}]

    def test_transparent_background_reaches_the_capture(self, rig):
        gs.handler(transparent_background=True)
        assert rig[0]["transparent_background"] is True
        assert rig[0]["anti_aliased"] is None       # the switch not asked for stays absent

    def test_anti_aliased_false_is_forwarded_not_dropped(self, rig):
        # False must survive as False, not collapse to "unset" - otherwise an explicit
        # anti_aliased=False silently renders anti-aliased.
        gs.handler(anti_aliased=False)
        assert rig[0]["anti_aliased"] is False
        assert rig[0]["transparent_background"] is None

    def test_both_switches_forwarded_together(self, rig):
        gs.handler(transparent_background=False, anti_aliased=True)
        assert rig[0]["transparent_background"] is False and rig[0]["anti_aliased"] is True

    def test_image_content_block_still_returned(self, rig):
        result = gs.handler(transparent_background=True)
        assert [c["type"] for c in result["content"]] == ["image"]
        assert result["content"][0]["data"] == "B64DATA"

    def test_capture_failure_is_an_error(self, rig, monkeypatch):
        monkeypatch.setattr(gs._view_common, "capture_png_b64",
                            lambda *a, **k: (None, "Viewport capture failed."))
        result = gs.handler(transparent_background=True)
        assert result["isError"] is True and "capture failed" in result["message"]


class TestFitToRestoreDisclosure:
    """fit_to hides the other occurrences to frame one - a mutation a READ tool must undo. A
    restore that did not take is surfaced on the result, never swallowed in a finally."""

    @pytest.fixture
    def rig(self, monkeypatch):
        vp = SimpleNamespace(camera=SimpleNamespace(viewExtents=1.0), fit=lambda: None)
        monkeypatch.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        monkeypatch.setattr(gs._common, "design", lambda: None)
        monkeypatch.setattr(gs._view_common, "capture_png_b64",
                            lambda *a, **k: ("B64DATA", None))
        monkeypatch.setattr(gs._view_common, "apply_named_view", lambda v, name: None)
        return monkeypatch

    def _stub_isolate(self, monkeypatch, stuck):
        monkeypatch.setattr(gs, "_isolate_for_fit",
                            lambda name: (lambda: list(stuck), object(), None))

    def test_a_clean_restore_leaves_the_image_alone(self, rig):
        self._stub_isolate(rig, [])
        result = gs.handler(fit_to="Bracket:1")
        assert result["isError"] is False
        assert [c["type"] for c in result["content"]] == ["image"]

    def test_a_failed_restore_rides_on_the_successful_shot(self, rig):
        self._stub_isolate(rig, ["Sub:1+Gear:1"])
        result = gs.handler(fit_to="Bracket:1")
        assert result["isError"] is False              # the picture WAS taken
        text = result["content"][0]["text"]
        assert "Sub:1+Gear:1" in text and "view_set" in text
        assert [c["type"] for c in result["content"]] == ["text", "image"]

    def test_a_restore_that_raises_is_reported_not_swallowed(self, rig):
        def boom():
            raise RuntimeError("occurrence went invalid")
        rig.setattr(gs, "_isolate_for_fit", lambda name: (boom, object(), None))
        result = gs.handler(fit_to="Bracket:1")
        assert "occurrence went invalid" in result["content"][0]["text"]

    def test_a_failed_orient_still_names_the_bulb_it_could_not_restore(self, rig):
        # The orient blows up AFTER fit_to hid the others. This exit returns before the capture
        # block, so without its own restore disclosure a stuck bulb is named nowhere at all while
        # the document is left with occurrences hidden.
        self._stub_isolate(rig, ["Sub:1+Gear:1"])
        rig.setattr(gs._view_common, "apply_named_view",
                    lambda v, name: (_ for _ in ()).throw(RuntimeError("camera is busy")))
        result = gs.handler(view="top", fit_to="Bracket:1")
        assert result["isError"] is True
        assert "Failed to set view 'top'" in result["message"]
        assert "Sub:1+Gear:1" in result["message"] and "view_set" in result["message"]

    def test_a_failed_orient_with_a_clean_restore_says_nothing_extra(self, rig):
        self._stub_isolate(rig, [])
        rig.setattr(gs._view_common, "apply_named_view",
                    lambda v, name: (_ for _ in ()).throw(RuntimeError("camera is busy")))
        result = gs.handler(view="top", fit_to="Bracket:1")
        assert result["isError"] is True and "could NOT turn" not in result["message"]

    def test_a_failed_restore_is_appended_to_a_capture_error_too(self, rig):
        self._stub_isolate(rig, ["Sub:1+Gear:1"])
        rig.setattr(gs._view_common, "capture_png_b64",
                    lambda *a, **k: (None, "Viewport capture failed."))
        result = gs.handler(fit_to="Bracket:1")
        assert result["isError"] is True
        assert "capture failed" in result["message"] and "Sub:1+Gear:1" in result["message"]

    def test_the_description_discloses_the_hide_and_restore(self):
        assert "hides the others" in gs.TOOL_DESCRIPTION
        assert "restores them" in gs.TOOL_DESCRIPTION


class TestCameraRestore:
    """The camera this tool moves to take its picture is the USER's. Every exit puts it back, and a
    restore (or a zoom) the viewport refuses is NAMED, never swallowed."""

    class _Viewport:
        """A viewport whose camera READ or camera assignment can be made to raise, modelling a
        platform that refuses the snapshot, or refuses the restore after accepting the move."""

        class _StuckExtents:
            """A camera whose viewExtents will not be written - the zoom the platform drops."""
            @property
            def viewExtents(self):
                return 1.0

            @viewExtents.setter
            def viewExtents(self, value):
                raise RuntimeError("extents locked")

        def __init__(self, refuse_assign=False, zoom_raises=False, camera_unreadable=False):
            self._refuse = refuse_assign
            self._unreadable = camera_unreadable
            self._camera = (self._StuckExtents() if zoom_raises
                            else SimpleNamespace(viewExtents=1.0))
            self.assigned = []

        @property
        def camera(self):
            if self._unreadable:
                raise RuntimeError("camera unavailable")
            return self._camera

        @camera.setter
        def camera(self, value):
            if self._refuse:
                raise RuntimeError("viewport busy")
            self.assigned.append(value)
            self._camera = value

        def fit(self):
            pass

    @pytest.fixture
    def rig(self, monkeypatch):
        monkeypatch.setattr(gs._common, "design", lambda: None)
        monkeypatch.setattr(gs._view_common, "capture_png_b64",
                            lambda *a, **k: ("B64DATA", None))
        return monkeypatch

    def test_a_failed_orient_puts_the_camera_back(self, rig):
        # apply_named_view moves the camera and THEN fails; returning without restoring leaves the
        # user's viewport somewhere they never asked for, with no way back.
        vp = self._Viewport()
        original = vp.camera
        rig.setattr(gs, "app", SimpleNamespace(activeViewport=vp))

        def move_then_fail(viewport, name):
            viewport.camera = SimpleNamespace(viewExtents=99.0)   # the camera HAS moved
            raise RuntimeError("camera is busy")
        rig.setattr(gs._view_common, "apply_named_view", move_then_fail)
        result = gs.handler(view="top")
        assert result["isError"] is True and "Failed to set view 'top'" in result["message"]
        assert vp.camera is original                              # put back on the failure exit

    def test_a_camera_restore_the_viewport_refuses_is_named_on_the_shot(self, rig):
        vp = self._Viewport(refuse_assign=True)
        rig.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        rig.setattr(gs._view_common, "apply_named_view", lambda v, name: None)
        result = gs.handler(view="top")
        assert result["isError"] is False                         # the picture WAS taken
        text = result["content"][0]["text"]
        assert "could NOT be put back" in text and "viewport busy" in text
        assert "view_set(orient)" in text

    def test_a_clean_restore_says_nothing_extra(self, rig):
        vp = self._Viewport()
        rig.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        rig.setattr(gs._view_common, "apply_named_view", lambda v, name: None)
        result = gs.handler(view="top")
        assert [c["type"] for c in result["content"]] == ["image"]

    def test_a_camera_that_will_not_snapshot_says_the_view_is_LEFT_where_the_shot_put_it(self, rig):
        # The snapshot is the only thing that makes the move undoable. When the camera cannot be
        # READ the orient still happens, so the payload owes the caller the fact that the viewport
        # is parked at the capture view - silence reads as "your view came back".
        vp = self._Viewport(camera_unreadable=True)
        rig.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        oriented = []
        rig.setattr(gs._view_common, "apply_named_view", lambda v, name: oriented.append(name))
        result = gs.handler(view="front")
        assert result["isError"] is False and oriented == ["front"]   # the camera DID move
        assert [c["type"] for c in result["content"]] == ["text", "image"]
        text = result["content"][0]["text"]
        assert "LEFT at the capture view" in text
        assert "view_set(orient)" in text

    def test_a_zoom_the_camera_refuses_is_disclosed_not_silently_dropped(self, rig):
        # The caller asked for zoom=0.5; a swallowed failure returns the FITTED frame as if the
        # zoom had applied, and the image is read as the requested framing.
        vp = self._Viewport(zoom_raises=True)
        rig.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        rig.setattr(gs._view_common, "apply_named_view", lambda v, name: None)
        result = gs.handler(view="top", zoom=0.5)
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "zoom=0.5 could NOT be applied" in text and "extents locked" in text


class TestFilePathWrite:
    """'file_path' writes the captured PNG to local disk - the raster file drawing_insert_image
    needs. The inline image is returned either way; a file that does not land is a failure, since
    the caller asked for a path to hand on."""

    PNG = b"\x89PNG\r\n\x1a\nSTUB-BYTES"

    @pytest.fixture
    def rig(self, monkeypatch):
        vp = SimpleNamespace(camera=SimpleNamespace(viewExtents=1.0), fit=lambda: None)
        monkeypatch.setattr(gs, "app", SimpleNamespace(activeViewport=vp))
        monkeypatch.setattr(gs._common, "design", lambda: None)
        oriented = []
        monkeypatch.setattr(gs._view_common, "apply_named_view",
                            lambda v, name: oriented.append(name))
        monkeypatch.setattr(gs._view_common, "capture_png_b64",
                            lambda *a, **k: (base64.b64encode(self.PNG).decode("ascii"), None))
        return SimpleNamespace(monkeypatch=monkeypatch, oriented=oriented)

    def _text(self, result):
        return " ".join(c["text"] for c in result["content"] if c["type"] == "text")

    def test_the_captured_png_bytes_land_on_disk(self, rig, tmp_path):
        out = str(tmp_path / "shot.png")
        result = gs.handler(file_path=out)
        assert result["isError"] is False
        # the file carries the CAPTURED bytes, not the base64 text of them
        assert open(out, "rb").read() == self.PNG

    def test_the_path_and_size_are_published_beside_the_image(self, rig, tmp_path):
        out = str(tmp_path / "shot.png")
        result = gs.handler(file_path=out)
        text = self._text(result)
        assert f"file_path={out}" in text
        assert f"size_bytes={len(self.PNG)}" in text
        assert [c["type"] for c in result["content"]] == ["text", "image"]

    def test_omitting_file_path_writes_nothing_and_returns_the_image_alone(self, rig, tmp_path):
        result = gs.handler()
        assert [c["type"] for c in result["content"]] == ["image"]
        assert list(tmp_path.iterdir()) == []

    def test_a_missing_png_extension_is_appended(self, rig, tmp_path):
        # the capture IS a PNG whatever the path says - the fleet's export idiom appends rather
        # than leaving PNG bytes under another format's name
        result = gs.handler(file_path=str(tmp_path / "shot.jpg"))
        assert (tmp_path / "shot.jpg.png").read_bytes() == self.PNG
        assert "shot.jpg.png" in self._text(result)

    def test_an_existing_png_extension_is_not_doubled(self, rig, tmp_path):
        gs.handler(file_path=str(tmp_path / "SHOT.PNG"))
        assert (tmp_path / "SHOT.PNG").exists()
        assert not (tmp_path / "SHOT.PNG.png").exists()

    def test_a_missing_output_directory_is_created(self, rig, tmp_path):
        out = str(tmp_path / "renders" / "deep" / "shot.png")
        result = gs.handler(file_path=out)
        assert result["isError"] is False and os.path.isfile(out)

    def test_an_uncreatable_directory_refuses_before_the_camera_moves(self, rig, tmp_path):
        # the refusal costs the caller nothing: a read tool that reoriented the view and then
        # failed would have moved the user's camera for no picture at all
        rig.monkeypatch.setattr(gs._export.os, "makedirs",
                                lambda *a, **k: (_ for _ in ()).throw(OSError("read-only volume")))
        result = gs.handler(view="top", file_path=str(tmp_path / "nope" / "shot.png"))
        assert result["isError"] is True
        assert "read-only volume" in result["message"] and "nope" in result["message"]
        assert rig.oriented == []

    def test_a_zero_byte_write_is_a_failure_not_an_ok_carrying_the_image(self, rig, tmp_path):
        # a captured-but-empty file is the false success this gate exists for: the caller would be
        # sent to a path holding nothing
        rig.monkeypatch.setattr(gs._view_common, "capture_png_b64", lambda *a, **k: ("", None))
        out = str(tmp_path / "shot.png")
        result = gs.handler(file_path=out)
        assert result["isError"] is True
        assert "size_bytes=0" in result["message"] and out in result["message"]

    def test_an_undecodable_capture_is_reported_naming_the_path(self, rig, tmp_path):
        rig.monkeypatch.setattr(gs._view_common, "capture_png_b64", lambda *a, **k: ("ABC", None))
        out = str(tmp_path / "shot.png")
        result = gs.handler(file_path=out)
        assert result["isError"] is True
        assert "could not be decoded" in result["message"] and out in result["message"]

    def test_a_capture_failure_writes_no_file(self, rig, tmp_path):
        # the capture's OWN error must survive to the caller: attempting the decode/write on a
        # failed capture would relabel it as a decode failure and point at the wrong cause
        rig.monkeypatch.setattr(gs._view_common, "capture_png_b64",
                                lambda *a, **k: (None, "Viewport capture failed."))
        out = str(tmp_path / "shot.png")
        result = gs.handler(file_path=out)
        assert result["isError"] is True
        assert "capture failed" in result["message"]
        assert "could not be decoded" not in result["message"]
        assert not os.path.exists(out)

    def test_the_users_camera_is_restored_after_a_reoriented_shot(self, rig, tmp_path):
        # the shot reorients the camera; a read the user sees as a moved view is a side effect
        # this tool undoes - the snapshot goes back whether or not a file was requested
        vp = gs.app.activeViewport
        snapshot = vp.camera

        def orient(viewport, name):
            viewport.camera = SimpleNamespace(viewExtents=9.0)    # the orient moves the camera

        rig.monkeypatch.setattr(gs._view_common, "apply_named_view", orient)
        gs.handler(view="top", file_path=str(tmp_path / "shot.png"))
        assert vp.camera is snapshot

    def test_the_surface_offers_the_path_and_says_the_image_still_returns(self):
        props = gs.tool.to_dict()["inputSchema"]["properties"]
        assert props["file_path"]["type"] == "string"
        assert ".png" in props["file_path"]["description"]
        assert "file_path" in gs.TOOL_DESCRIPTION and "inline" in gs.TOOL_DESCRIPTION


class TestKeepVisible:
    def test_target_itself_kept(self):
        assert gs._keep_visible("Frame:1", "Frame:1") is True

    def test_ancestor_kept(self):
        # hiding Frame:1 would hide its nested child - the exact nested-target BLANK cause
        assert gs._keep_visible("Frame:1", "Frame:1+Pedestal:1") is True

    def test_descendant_kept(self):
        assert gs._keep_visible("Frame:1+Pedestal:1", "Frame:1") is True

    def test_sibling_hidden(self):
        assert gs._keep_visible("Gear:1", "Frame:1") is False

    def test_string_prefix_is_not_a_path_boundary(self):
        # 'Frame:10' is a different instance, NOT an ancestor of 'Frame:1' - the '+' boundary matters
        assert gs._keep_visible("Frame:10", "Frame:1") is False
        assert gs._keep_visible("Frame:1", "Frame:10") is False

    def test_missing_path_is_hidden(self):
        assert gs._keep_visible(None, "Frame:1") is False
        assert gs._keep_visible("Frame:1", None) is False
