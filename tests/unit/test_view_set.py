"""Unit tests for ``view_set.py`` — the agent's view verbs.

Camera math (eye/target/up per orientation) is a live-viewport side-effect best
left to a real session, but the surrounding LOGIC is pure and worth pinning:
action validation, occurrence resolution via the shared typed kinds (exact fullPathName/name beats
substring; an ambiguous substring is REFUSED, never grabbed first-match; isolate needs exactly one
resolved match and stays occurrence-only), the visibility verbs (isolate flag, hide bulb,
clear_isolation, show lighting the whole ancestor chain, body-level hide/show with a per-body
bulb read-back), style validation, the
named-view library (save overwrites same name, apply errors with a list, list
reports built-ins), and the snapshot -> mutate -> restore round-trip that puts
bulbs/isolation/style back. Fakes expose the real attributes the tool sets so we
can assert on them.
"""

import json

from conftest import load_tool, BRepBody, _NamedCollection

iv = load_tool("view_set")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakePoint:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class FakeBBox:
    def __init__(self, minp, maxp):
        self.minPoint = FakePoint(*minp)
        self.maxPoint = FakePoint(*maxp)


class FakeOcc:
    def __init__(self, name, full_path=None, bbox=None, parent=None,
                 bulb=True, isolated=False):
        self.name = name
        self.fullPathName = full_path or name
        self.boundingBox = bbox
        self.assemblyContext = parent
        self.isLightBulbOn = bulb
        self.isIsolated = isolated
        self.isVisible = bulb


class FakeRoot:
    def __init__(self, occurrences, bodies=()):
        self.allOccurrences = list(occurrences)
        # root-level bodies (conftest.BRepBody instances) - the body-level hide/show targets.
        self.bRepBodies = _NamedCollection(bodies)


class FakeNamedView:
    def __init__(self, name, built_in=False):
        self.name = name
        self.isBuiltIn = built_in
        self._deleted = False
        self.applied = False
        self._owner = None          # set when added to a FakeNamedViews

    def deleteMe(self):
        self._deleted = True
        # Mirror Fusion: deleting a named view removes it from its collection.
        if self._owner is not None and self in self._owner._views:
            self._owner._views.remove(self)
        return True

    def apply(self):
        self.applied = True


class FakeNamedViews:
    def __init__(self, views=()):
        self._views = list(views)
        for v in self._views:
            v._owner = self

    @property
    def count(self):
        return len(self._views)

    def item(self, i):
        return self._views[i]

    def itemByName(self, name):
        for v in self._views:
            if v.name == name:
                return v
        raise RuntimeError("not found")   # Fusion throws when absent

    def add(self, _camera, name):
        nv = FakeNamedView(name)
        nv._owner = self
        self._views.append(nv)
        return nv


class FakeDesign:
    def __init__(self, occurrences, named_views=None, bodies=()):
        self.rootComponent = FakeRoot(occurrences, bodies)
        self.namedViews = named_views if named_views is not None else FakeNamedViews()


class FakeCamera:
    def __init__(self):
        import adsk.core
        self.eye = FakePoint(10, 10, 10)
        self.target = FakePoint(0, 0, 0)
        self.upVector = None
        self.isFitView = False
        # a camera starts orthographic here; the projection tests are what flip it
        self.cameraType = adsk.core.CameraTypes.OrthographicCameraType
        self.perspectiveAngle = 0.0


class FakeViewport:
    def __init__(self):
        self.camera = FakeCamera()
        self.visualStyle = 0

    def refresh(self):
        pass


class _StubbornViewport:
    """A viewport that does not fully honour a camera assignment: every camera READ hands back a
    fresh camera carrying the projection (and optionally the perspective angle) this viewport
    insists on. Models the two states the orient read-back gates on - a projection set that never
    took, and a perspective angle the camera settles on somewhere else."""

    def __init__(self, camera_type, perspective_angle=None, angle_readable=True,
                 type_readable=True):
        self._type = camera_type
        self._angle = perspective_angle
        self._angle_readable = angle_readable
        self._type_readable = type_readable
        self.assigned = None
        self.visualStyle = 0

    @property
    def camera(self):
        cam = FakeCamera()
        cam.cameraType = self._type
        if self._angle is not None:
            cam.perspectiveAngle = self._angle
        if not self._angle_readable:
            del cam.perspectiveAngle      # the property is unreadable, which is not an angle of 0
        if not self._type_readable:
            del cam.cameraType            # unreadable, which is not a projection that failed
        return cam

    @camera.setter
    def camera(self, value):
        self.assigned = value

    def refresh(self):
        pass


class FakeDataFile:
    def __init__(self, file_id):
        self.id = file_id


class FakeDoc:
    def __init__(self, name, data_file_id=None):
        self.name = name
        if data_file_id is not None:
            self.dataFile = FakeDataFile(data_file_id)


class FakeApp:
    def __init__(self, design, doc_name="Doc", doc_id=None):
        self.activeProduct = design
        self.activeDocument = FakeDoc(doc_name, data_file_id=doc_id)
        self.activeViewport = FakeViewport()


def _body(name, bulb=True, hidden_by_ancestor=False):
    """A conftest BRepBody: own settable bulb; isVisible = bulb AND not hidden_by_ancestor."""
    return BRepBody(name=name, light_bulb=bulb, hidden_by_ancestor=hidden_by_ancestor)


def _install(monkeypatch, occurrences=(), named_views=None, doc_name="Doc", doc_id=None, bodies=()):
    design = FakeDesign(list(occurrences), named_views, bodies)
    app = FakeApp(design, doc_name, doc_id=doc_id)
    monkeypatch.setattr(iv, "app", app)
    monkeypatch.setattr(iv._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    # adsk.core.VisualStyles.<Name> must resolve to an int for _do_style.
    import adsk.core
    vs = adsk.core.VisualStyles
    for i, attr in enumerate(["ShadedVisualStyle", "WireframeVisualStyle",
                              "ShadedWithVisibleEdgesOnlyVisualStyle",
                              "ShadedWithHiddenEdgesVisualStyle",
                              "WireframeWithHiddenEdgesVisualStyle",
                              "WireframeWithVisibleEdgesOnlyVisualStyle"]):
        monkeypatch.setattr(vs, attr, i + 1, raising=False)
    # Point3D/Vector3D create -> simple carriers (orient math touches these).
    monkeypatch.setattr(adsk.core.Point3D, "create", staticmethod(lambda x, y, z: FakePoint(x, y, z)))
    monkeypatch.setattr(adsk.core.Vector3D, "create", staticmethod(lambda x, y, z: FakePoint(x, y, z)))
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self, monkeypatch):
        _install(monkeypatch)
        res = iv.handler(action="zoomzoom")
        assert res["isError"] is True and "Unknown action" in res["message"]

    def test_no_design(self, monkeypatch):
        app = FakeApp(None)
        monkeypatch.setattr(iv, "app", app)
        monkeypatch.setattr(iv._common, "app", app)
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: None)
        res = iv.handler(action="orient", orientation="front")
        assert res["isError"] is True and "No active design" in res["message"]


# ── occurrence resolution via visibility verbs ──────────────────────────────

class TestVisibility:
    def test_hide_turns_bulb_off(self, monkeypatch):
        occ = FakeOcc("Bracket", bulb=True)
        _install(monkeypatch, [occ])
        _payload(iv.handler(action="hide", target="Bracket"))
        assert occ.isLightBulbOn is False

    def test_isolate_sets_flag(self, monkeypatch):
        occ = FakeOcc("Bracket")
        _install(monkeypatch, [occ])
        _payload(iv.handler(action="isolate", target="Bracket"))
        assert occ.isIsolated is True

    def test_isolate_requires_single_match(self, monkeypatch):
        # two EXPLICIT, unambiguous targets in one list -> each resolves fine, but isolate refuses
        # more than one match (this is the "needs exactly one" guard, distinct from ambiguity refusal).
        a = FakeOcc("Bolt:1", full_path="Sub1+Bolt:1")
        b = FakeOcc("Bolt:2", full_path="Sub2+Bolt:2")
        _install(monkeypatch, [a, b])
        res = iv.handler(action="isolate", target=["Sub1+Bolt:1", "Sub2+Bolt:2"])
        assert res["isError"] is True
        assert "needs exactly one" in res["message"]

    def test_ambiguous_substring_refused_not_first_match(self, monkeypatch):
        # a bare substring matching SEVERAL occurrences must ERROR (naming the ambiguity), never
        # silently act on the first one - the wrong-instance risk OccurrenceRefList exists to refuse.
        a = FakeOcc("Bolt:1")
        b = FakeOcc("Bolt:2")
        _install(monkeypatch, [a, b])
        res = iv.handler(action="hide", target="Bolt")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert a.isLightBulbOn is True and b.isLightBulbOn is True   # neither touched

    def test_exact_name_beats_substring(self, monkeypatch):
        exact = FakeOcc("Bolt")
        longer = FakeOcc("Bolt Flange")
        _install(monkeypatch, [longer, exact])
        # 'Bolt' exact-matches one, substring-matches both; exact wins -> single isolate ok
        out = _payload(iv.handler(action="isolate", target="Bolt"))
        assert out["affected"] == ["Bolt"]
        assert exact.isIsolated is True
        assert longer.isIsolated is False

    def test_show_lights_ancestor_chain(self, monkeypatch):
        parent = FakeOcc("Assembly", bulb=False)
        child = FakeOcc("Screw", full_path="Assembly+Screw", parent=parent, bulb=False)
        _install(monkeypatch, [parent, child])
        out = _payload(iv.handler(action="show", target="Screw"))
        assert child.isLightBulbOn is True
        assert parent.isLightBulbOn is True          # ancestor lit too
        assert "Assembly" in out["ancestors_also_shown"]

    def test_clear_isolation_resets_all(self, monkeypatch):
        a = FakeOcc("A", isolated=True)
        b = FakeOcc("B", isolated=True)
        _install(monkeypatch, [a, b])
        out = _payload(iv.handler(action="clear_isolation"))
        assert out["cleared_count"] == 2
        assert a.isIsolated is False and b.isIsolated is False

    def test_unmatched_target_errors(self, monkeypatch):
        # hide resolves through the occurrence-or-body kind, so a full miss reports the whole contract
        # (not just occurrences).
        _install(monkeypatch, [FakeOcc("A")])
        res = iv.handler(action="hide", target="Ghost")
        assert res["isError"] is True and "did not resolve" in res["message"].lower()

    def test_missing_target_errors(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("A")])
        res = iv.handler(action="hide")
        assert res["isError"] is True and "Provide 'target'" in res["message"]


class TestBodyVisibility:
    """hide/show reach single BODIES (root-level bodies / one body of a multi-body component) -
    the granularity occurrence bulbs cannot address. The bulb write is READ BACK per body."""

    def test_hide_one_root_body_leaves_the_other_lit(self, monkeypatch):
        b1, b2 = _body("Body1"), _body("Body2")
        _install(monkeypatch, bodies=[b1, b2])
        out = _payload(iv.handler(action="hide", target=["Body1"]))
        assert b1.isLightBulbOn is False
        assert b2.isLightBulbOn is True          # the sibling body is untouched
        assert out["affected"] == ["Body1"]
        assert out["bodies"] == [{"body": "Body1", "light_bulb_on": False, "visible": False}]

    def test_show_body_turns_bulb_on_and_reads_back(self, monkeypatch):
        b = _body("Body1", bulb=False)
        _install(monkeypatch, bodies=[b])
        out = _payload(iv.handler(action="show", target=["Body1"]))
        assert b.isLightBulbOn is True
        assert out["bodies"][0]["light_bulb_on"] is True

    def test_body_bulb_write_that_does_not_take_errors(self, monkeypatch):
        # Honesty gate: a bulb set the platform swallows must be an error, never a false ok.
        class _StubbornBody:
            name = "Body1"
            entityToken = "Body1"
            isVisible = True
            @property
            def isLightBulbOn(self):
                return True
            @isLightBulbOn.setter
            def isLightBulbOn(self, v):
                pass                             # silently ignores the write
        _install(monkeypatch, bodies=[_StubbornBody()])
        res = iv.handler(action="hide", target=["Body1"])
        assert res["isError"] is True and "reads back" in res["message"]

    def test_shown_but_still_invisible_body_is_named_in_the_note(self, monkeypatch):
        # bulb ON but isVisible False (an ancestor occurrence is dark) - report it, don't claim done.
        b = _body("Body1", bulb=False, hidden_by_ancestor=True)
        _install(monkeypatch, bodies=[b])
        out = _payload(iv.handler(action="show", target=["Body1"]))
        assert out["bodies"][0] == {"body": "Body1", "light_bulb_on": True, "visible": False}
        assert "visible:false" in out["note"]

    def test_mixed_occurrence_and_body_hide(self, monkeypatch):
        occ = FakeOcc("Bracket", bulb=True)
        b = _body("Body1")
        _install(monkeypatch, [occ], bodies=[b])
        out = _payload(iv.handler(action="hide", target=["Bracket", "Body1"]))
        assert occ.isLightBulbOn is False and b.isLightBulbOn is False
        assert out["affected"] == ["Bracket", "Body1"]
        assert [r["body"] for r in out["bodies"]] == ["Body1"]   # read-back rows are bodies only

    def test_hide_via_face_handle_walks_to_owning_body(self, monkeypatch):
        # find_geometry mints only face/edge/vertex handles - never a body handle - so a body target
        # arrives as a FACE handle and must resolve to the face's OWNING body (TargetRef's owner-walk;
        # also how an ambiguous body NAME is disambiguated, per the refusal's own advice).
        b = _body("Body1")
        face = type("F", (), {})()
        face.body = b
        design = _install(monkeypatch, bodies=[b])
        design.findEntityByToken = lambda tok: [face] if tok == "TOK_FACE" else []
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion, "BRepFace", type(face), raising=False)
        out = _payload(iv.handler(action="hide", target=["TOK_FACE"]))
        assert b.isLightBulbOn is False
        assert out["bodies"][0]["body"] == "Body1"

    def test_isolate_refuses_a_body_target(self, monkeypatch):
        # isolate stays occurrence-granular (Fusion has no body isolate) - a body name must not resolve.
        _install(monkeypatch, bodies=[_body("Body1")])
        res = iv.handler(action="isolate", target=["Body1"])
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()


# ── style ────────────────────────────────────────────────────────────────────

class TestStyle:
    def test_wireframe_sets_visual_style(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(iv.handler(action="style", style="wireframe"))
        assert out["style"] == "wireframe"
        # 'wireframe' -> WireframeVisualStyle, seeded 2 in _install: the viewport must hold THAT
        # enum and the payload must report it (not merely echo wherever the fake ended up)
        assert iv.app.activeViewport.visualStyle == 2
        assert out["visual_style_after"] == 2

    def test_unknown_style_errors(self, monkeypatch):
        _install(monkeypatch)
        res = iv.handler(action="style", style="crayon")
        assert res["isError"] is True and "Provide 'style'" in res["message"]


# ── orient ───────────────────────────────────────────────────────────────────

class TestOrient:
    def test_unknown_orientation_errors(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        res = iv.handler(action="orient", orientation="sideways")
        assert res["isError"] is True and "Unknown orientation" in res["message"]

    def test_focus_unknown_occurrence_errors(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("Part")])
        res = iv.handler(action="orient", orientation="front", focus="Ghost")
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_front_orientation_sets_up_vector(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        out = _payload(iv.handler(action="orient", orientation="front", focus="Part"))
        assert out["applied"]["orientation"] == "front"
        assert out["applied"]["focus"] == "Part"
        # front up vector is +Z per _ORIENTATIONS
        up = iv.app.activeViewport.camera.upVector
        assert (up.x, up.y, up.z) == (0, 0, 1)

    def test_front_eye_placed_on_minus_y_at_preserved_distance(self, monkeypatch):
        # focus Part centered at (1,1,1); front view_dir = (0,-1,0). The default camera sits at
        # eye (10,10,10) target (0,0,0): distance sqrt(300) ~= 17.32. Eye must land at
        # target + dir*dist = (1, 1 - dist, 1) — pure -Y from the (now re-targeted) center.
        import math
        _install(monkeypatch, [FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        _payload(iv.handler(action="orient", orientation="front", focus="Part"))
        cam = iv.app.activeViewport.camera
        dist = math.sqrt(300)
        assert cam.target.x == 1 and cam.target.y == 1 and cam.target.z == 1
        assert cam.eye.x == 1
        assert math.isclose(cam.eye.y, 1 - dist, rel_tol=1e-9)
        assert cam.eye.z == 1

    def test_top_orientation_uses_plus_y_up(self, monkeypatch):
        # 'top' view_dir = (0,0,1), up = (0,1,0) (Y-up because the look axis IS Z).
        _install(monkeypatch, [FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        _payload(iv.handler(action="orient", orientation="top", focus="Part"))
        up = iv.app.activeViewport.camera.upVector
        assert (up.x, up.y, up.z) == (0, 1, 0)

    def test_focus_only_translates_eye_by_target_delta(self, monkeypatch):
        # No orientation, just focus -> the eye is SHIFTED by the same vector the target moved, so the
        # view DIRECTION is preserved (camera tracks to the new center without rotating).
        _install(monkeypatch, [FakeOcc("Part", bbox=FakeBBox((0, 0, 4), (2, 2, 8)))])  # center (1,1,6)
        # default camera: eye (10,10,10), target (0,0,0). target moves to (1,1,6): delta (1,1,6).
        _payload(iv.handler(action="orient", focus="Part"))
        cam = iv.app.activeViewport.camera
        assert (cam.target.x, cam.target.y, cam.target.z) == (1, 1, 6)
        # eye shifted by the same delta -> (11, 11, 16)
        assert (cam.eye.x, cam.eye.y, cam.eye.z) == (11, 11, 16)


# ── camera projection ───────────────────────────────────────────────────────
# 'projection' maps a wire key onto an adsk.core.CameraTypes member and is READ BACK off the
# viewport camera; 'perspective_angle_deg' is a degrees-in / radians-to-the-API field of view that
# only a perspective camera accepts.

class TestProjection:
    def _part(self):
        return FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))

    def test_each_key_maps_to_its_own_camera_types_member(self, monkeypatch):
        import adsk.core
        expected = {"orthographic": adsk.core.CameraTypes.OrthographicCameraType,
                    "perspective": adsk.core.CameraTypes.PerspectiveCameraType}
        assert len(set(expected.values())) == 2          # two distinct members, not one alias
        for key, member in expected.items():
            _install(monkeypatch, [self._part()])
            out = _payload(iv.handler(action="orient", projection=key))
            assert iv.app.activeViewport.camera.cameraType == member, key
            assert out["applied"]["projection"] == key

    def test_unknown_projection_is_refused_listing_the_valid_keys(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        res = iv.handler(action="orient", projection="fisheye")
        assert res["isError"] is True
        assert "Unknown projection 'fisheye'" in res["message"]
        assert "orthographic, perspective" in res["message"]

    def test_perspective_ortho_faces_is_not_offered(self, monkeypatch):
        # An assigned PerspectiveWithOrthoFaces camera reads back as PerspectiveCameraType (wrote
        # 2, read 1, measured with isFitView set), so the key is REFUSED rather than offered and
        # silently downgraded - offering it would trip the read-back mismatch on a correct call.
        assert list(iv._PROJECTIONS) == ["orthographic", "perspective"]      # the schema's enum
        _install(monkeypatch, [self._part()])
        res = iv.handler(action="orient", projection="perspective_ortho_faces")
        assert res["isError"] is True
        assert "Unknown projection 'perspective_ortho_faces'" in res["message"]
        assert "orthographic, perspective" in res["message"]

    def test_projection_combines_with_an_orientation_in_one_call(self, monkeypatch):
        import adsk.core
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", orientation="front", projection="perspective",
                                  focus="Part"))
        assert out["applied"]["orientation"] == "front"
        assert out["applied"]["projection"] == "perspective"
        cam = iv.app.activeViewport.camera
        assert cam.cameraType == adsk.core.CameraTypes.PerspectiveCameraType
        assert (cam.upVector.x, cam.upVector.y, cam.upVector.z) == (0, 0, 1)   # orientation kept

    def test_a_projection_that_does_not_take_is_an_error(self, monkeypatch):
        # the viewport swallows the assignment and keeps reading back orthographic - that must be
        # an error naming what it actually reads, never a false ok.
        import adsk.core
        _install(monkeypatch, [self._part()])
        monkeypatch.setattr(iv.app, "activeViewport",
                            _StubbornViewport(adsk.core.CameraTypes.OrthographicCameraType))
        res = iv.handler(action="orient", projection="perspective")
        assert res["isError"] is True
        assert "reads back 'orthographic'" in res["message"]
        assert "did not take" in res["message"]

    def test_an_unreadable_camera_type_is_an_error_not_a_mismatch_claim(self, monkeypatch):
        # an unreadable property is a different report from a read that shows the change did not
        # take - it must not be recast as a mismatch against a stringified None.
        import adsk.core
        _install(monkeypatch, [self._part()])
        monkeypatch.setattr(iv.app, "activeViewport",
                            _StubbornViewport(adsk.core.CameraTypes.PerspectiveCameraType,
                                              type_readable=False))
        res = iv.handler(action="orient", projection="perspective")
        assert res["isError"] is True
        assert "cameraType could not be read back" in res["message"]
        assert "projection is unverified" in res["message"]
        assert "did not take" not in res["message"]
        assert "None" not in res["message"]

    def test_untouched_projection_is_not_reported(self, monkeypatch):
        # an orient with no projection input must not claim a projection it never set
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", orientation="top", focus="Part"))
        assert "projection" not in out["applied"]

    def test_angle_only_call_does_not_claim_an_applied_projection(self, monkeypatch):
        import adsk.core
        _install(monkeypatch, [self._part()])
        iv.app.activeViewport.camera.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
        out = _payload(iv.handler(action="orient", perspective_angle_deg=40))
        assert "projection" not in out["applied"]        # nothing was applied to the projection
        assert out["applied"]["perspective_angle_deg"] == 40.0


class TestProjectionExtents:
    """Flipping cameraType leaves the camera's extents inconsistent with the type it now carries -
    assigning such a camera raises 'Camera type must be orthographic for extents' unless isFitView
    recomputes them. So the projection path fits whether or not the caller asked it to."""

    def _part(self):
        return FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))

    def test_projection_sets_isfitview_even_when_fit_is_false(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", projection="perspective", fit=False))
        assert iv.app.activeViewport.camera.isFitView is True
        assert "fitted even though fit was false" in out["note"]

    def test_a_plain_orient_still_honours_fit_false(self, monkeypatch):
        # no projection change -> no extents recompute is needed, so fit=false stays fit=false
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", orientation="front", focus="Part", fit=False))
        assert iv.app.activeViewport.camera.isFitView is False
        assert "fitted even though" not in out["note"]

    def test_fit_true_with_a_projection_says_nothing_extra(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", projection="perspective", fit=True))
        assert iv.app.activeViewport.camera.isFitView is True
        assert "fitted even though" not in out["note"]


class TestPerspectiveAngle:
    def _part(self):
        return FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))

    def test_degrees_reach_the_camera_as_radians(self, monkeypatch):
        # Camera.perspectiveAngle is radians (a fresh perspective camera reads 0.39479 = Fusion's
        # 22.62 deg default field of view), and a written angle survives the single camera
        # assignment bit-exactly - so the value on the camera is EXACTLY radians(45), and the
        # degree number itself never reaches the property.
        import math
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", projection="perspective",
                                  perspective_angle_deg=45))
        cam = iv.app.activeViewport.camera
        assert cam.perspectiveAngle == math.radians(45)
        assert cam.perspectiveAngle != 45
        assert out["applied"]["perspective_angle_deg"] == 45.0

    def test_angle_allowed_on_a_camera_already_in_perspective_ortho_faces(self, monkeypatch):
        # view_set cannot SET that mode - an assigned PerspectiveWithOrthoFaces camera reads back
        # as plain Perspective - but a camera ALREADY in it accepts a written angle exactly
        # (30 deg written, 30 deg read, live-measured), so the angle path must admit it.
        import adsk.core
        import math
        _install(monkeypatch, [self._part()])
        iv.app.activeViewport.camera.cameraType = (
            adsk.core.CameraTypes.PerspectiveWithOrthoFacesCameraType)
        out = _payload(iv.handler(action="orient", perspective_angle_deg=35))
        assert iv.app.activeViewport.camera.perspectiveAngle == math.radians(35)
        assert out["applied"]["perspective_angle_deg"] == 35.0

    def test_an_unreadable_camera_type_on_the_angle_path_names_no_projection(self, monkeypatch):
        # the precondition needs the camera's OWN type when no projection is given; when that read
        # fails there is no projection to name, so the refusal must say the read failed rather than
        # report a projection whose name is the missing read.
        import adsk.core
        _install(monkeypatch, [self._part()])
        monkeypatch.setattr(iv.app, "activeViewport",
                            _StubbornViewport(adsk.core.CameraTypes.PerspectiveCameraType,
                                              type_readable=False))
        res = iv.handler(action="orient", perspective_angle_deg=45)
        assert res["isError"] is True
        assert "cameraType could not be read" in res["message"]
        assert "None" not in res["message"]
        assert "projection='perspective'" in res["message"]

    def test_an_unreadable_angle_is_an_error_not_a_reported_zero(self, monkeypatch):
        # the read-back is a MEASUREMENT: when the property cannot be read, publishing 0.0 would
        # claim a field of view of zero degrees. Error naming the camera state instead.
        import adsk.core
        _install(monkeypatch, [self._part()])
        monkeypatch.setattr(iv.app, "activeViewport",
                            _StubbornViewport(adsk.core.CameraTypes.PerspectiveCameraType,
                                              angle_readable=False))
        res = iv.handler(action="orient", projection="perspective", perspective_angle_deg=45)
        assert res["isError"] is True
        assert "could not be read back" in res["message"]
        assert "'perspective'" in res["message"]         # names the camera state it found
        assert "0.0" not in res["message"]

    def test_angle_on_an_orthographic_projection_is_refused(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        res = iv.handler(action="orient", projection="orthographic", perspective_angle_deg=35)
        assert res["isError"] is True
        assert "35" in res["message"] and "'orthographic'" in res["message"]
        assert "perspective_ortho_faces" not in res["message"]   # not a value it can suggest
        assert iv.app.activeViewport.camera.perspectiveAngle == 0.0   # nothing was written

    def test_angle_without_a_projection_refuses_against_the_current_camera(self, monkeypatch):
        # no projection given and the camera is orthographic -> the conflict is with the camera's
        # OWN type, and the refusal names it rather than assuming a perspective camera.
        _install(monkeypatch, [self._part()])
        res = iv.handler(action="orient", perspective_angle_deg=35)
        assert res["isError"] is True and "'orthographic'" in res["message"]

    def test_angle_without_a_projection_is_allowed_on_a_perspective_camera(self, monkeypatch):
        import adsk.core
        import math
        _install(monkeypatch, [self._part()])
        iv.app.activeViewport.camera.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
        out = _payload(iv.handler(action="orient", perspective_angle_deg=45))
        assert math.isclose(iv.app.activeViewport.camera.perspectiveAngle, math.radians(45),
                            rel_tol=1e-9)
        assert out["applied"]["perspective_angle_deg"] == 45.0

    def test_angle_outside_the_accepted_range_is_refused(self, monkeypatch):
        # Fusion's setter accepts [1, 150) degrees and raises "3 : Invalid parameter value" outside
        # it, so the guard refuses by name rather than letting a raw platform error surface.
        for bad in (0, 0.99, -10, 150, 179.9, 200):
            _install(monkeypatch, [self._part()])
            res = iv.handler(action="orient", projection="perspective", perspective_angle_deg=bad)
            assert res["isError"] is True, bad
            assert str(float(bad)) in res["message"], bad
            assert "1 to just under 150" in res["message"], bad
            assert iv.app.activeViewport.camera.perspectiveAngle == 0.0, bad   # never written

    def test_the_accepted_boundaries_are_allowed(self, monkeypatch):
        # 1.0 is accepted (0.99 raises) and 149.99 is accepted (150 raises) - both live-measured,
        # so neither may be refused by our own guard.
        import math
        for good in (1, 149.99):
            _install(monkeypatch, [self._part()])
            out = _payload(iv.handler(action="orient", projection="perspective",
                                      perspective_angle_deg=good))
            assert out["applied"]["perspective_angle_deg"] == float(good), good
            assert iv.app.activeViewport.camera.perspectiveAngle == math.radians(good), good

    def test_non_numeric_angle_is_refused(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        res = iv.handler(action="orient", projection="perspective", perspective_angle_deg="wide")
        assert res["isError"] is True and "wide" in res["message"]

    def test_an_angle_the_camera_settles_elsewhere_reports_both(self, monkeypatch):
        # the camera comes back at 20 deg after a 45 deg request - report what it READS BACK and
        # name the requested value, rather than echoing the request as if it stuck.
        import adsk.core
        import math
        _install(monkeypatch, [self._part()])
        monkeypatch.setattr(iv.app, "activeViewport",
                            _StubbornViewport(adsk.core.CameraTypes.PerspectiveCameraType,
                                              perspective_angle=math.radians(20)))
        out = _payload(iv.handler(action="orient", projection="perspective",
                                  perspective_angle_deg=45))
        assert out["applied"]["perspective_angle_deg"] == 20.0
        assert out["applied"]["perspective_angle_requested_deg"] == 45.0
        assert "different perspective angle" in out["note"]

    def test_a_matching_angle_read_back_reports_no_divergence(self, monkeypatch):
        _install(monkeypatch, [self._part()])
        out = _payload(iv.handler(action="orient", projection="perspective",
                                  perspective_angle_deg=45))
        assert "perspective_angle_requested_deg" not in out["applied"]
        assert "different perspective angle" not in out["note"]


class TestProjectionOnlyOnOrient:
    def test_projection_on_another_action_is_refused(self, monkeypatch):
        # accepting it on 'style'/'snapshot' would report ok while setting no projection at all
        _install(monkeypatch, [FakeOcc("Part")])
        res = iv.handler(action="style", style="wireframe", projection="perspective")
        assert res["isError"] is True
        assert "action='style'" in res["message"]

    def test_perspective_angle_on_another_action_is_refused(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("Part")])
        res = iv.handler(action="list_views", perspective_angle_deg=45)
        assert res["isError"] is True and "action='list_views'" in res["message"]

    def test_other_actions_are_unaffected_when_the_inputs_are_absent(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("Part")])
        out = _payload(iv.handler(action="style", style="wireframe"))
        assert out["style"] == "wireframe"


# ── named views ──────────────────────────────────────────────────────────────

class TestNamedViews:
    def test_save_view_adds(self, monkeypatch):
        nvs = FakeNamedViews()
        _install(monkeypatch, [], named_views=nvs)
        out = _payload(iv.handler(action="save_view", view_name="MyAngle"))
        assert out["view_name"] == "MyAngle"
        assert nvs.count == 1

    def test_save_view_overwrites_same_name(self, monkeypatch):
        old = FakeNamedView("MyAngle")
        nvs = FakeNamedViews([old])
        _install(monkeypatch, [], named_views=nvs)
        _payload(iv.handler(action="save_view", view_name="MyAngle"))
        assert old._deleted is True          # old one removed before re-add
        assert nvs.count == 1                 # still just one "MyAngle"

    def test_save_view_requires_name(self, monkeypatch):
        _install(monkeypatch, [], named_views=FakeNamedViews())
        res = iv.handler(action="save_view")
        assert res["isError"] is True and "Provide 'view_name'" in res["message"]

    def test_apply_view_moves_camera(self, monkeypatch):
        nv = FakeNamedView("Home", built_in=True)
        _install(monkeypatch, [], named_views=FakeNamedViews([nv]))
        _payload(iv.handler(action="apply_view", view_name="Home"))
        assert nv.applied is True

    def test_apply_unknown_view_lists_available(self, monkeypatch):
        _install(monkeypatch, [], named_views=FakeNamedViews([FakeNamedView("Home")]))
        res = iv.handler(action="apply_view", view_name="Nope")
        assert res["isError"] is True
        assert "No named view 'Nope'" in res["message"]
        assert "Home" in res["message"]

    def test_list_views_reports_builtin_flag(self, monkeypatch):
        _install(monkeypatch, [], named_views=FakeNamedViews(
            [FakeNamedView("Home", built_in=True), FakeNamedView("Mine")]))
        out = _payload(iv.handler(action="list_views"))
        assert out["count"] == 2
        by = {v["name"]: v["built_in"] for v in out["named_views"]}
        assert by["Home"] is True and by["Mine"] is False


# ── snapshot / restore round-trip ───────────────────────────────────────────

class TestSnapshotRestore:
    def test_restore_without_snapshot_errors(self, monkeypatch):
        _install(monkeypatch, [FakeOcc("A")], doc_name="FreshDoc")
        iv._SNAPSHOTS.clear()
        res = iv.handler(action="restore")
        assert res["isError"] is True and "No snapshot saved" in res["message"]

    def test_snapshot_then_restore_puts_bulbs_back(self, monkeypatch):
        a = FakeOcc("A", full_path="A", bulb=True)
        b = FakeOcc("B", full_path="B", bulb=True)
        _install(monkeypatch, [a, b], doc_name="RoundTrip")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        # mutate after snapshot
        a.isLightBulbOn = False
        b.isLightBulbOn = False
        out = _payload(iv.handler(action="restore"))
        assert out["restored_occurrences"] == 2
        assert a.isLightBulbOn is True and b.isLightBulbOn is True
        # snapshot consumed (popped) on restore
        assert "RoundTrip" not in iv._SNAPSHOTS

    def test_restore_reinstates_isolation(self, monkeypatch):
        # snapshot captures an isolated occurrence; after clearing it, restore must put isolation back.
        a = FakeOcc("A", full_path="A", bulb=True, isolated=True)
        _install(monkeypatch, [a], doc_name="IsoRestore")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        a.isIsolated = False                 # user un-isolated after the snapshot
        out = _payload(iv.handler(action="restore"))
        assert out["restored_occurrences"] == 1
        assert a.isIsolated is True          # isolation reinstated from the snapshot

    def test_restore_counts_missing_occurrences(self, monkeypatch):
        a = FakeOcc("A", full_path="A")
        _install(monkeypatch, [a], doc_name="MissingTest")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        # remove 'A' from the design before restoring -> it's now missing
        iv.app.activeProduct.rootComponent.allOccurrences = []
        out = _payload(iv.handler(action="restore"))
        assert out["missing_occurrences"] == 1
        assert out["restored_occurrences"] == 0

    def test_a_snapshot_under_the_cap_makes_no_truncation_claim(self, monkeypatch):
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}") for i in range(3)]
        _install(monkeypatch, occs, doc_name="Small")
        iv._SNAPSHOTS.clear()
        out = _payload(iv.handler(action="snapshot"))
        assert out["occurrences_saved"] == 3
        assert "truncated" not in out and "PARTIAL" not in out["note"]

    def test_a_snapshot_past_the_cap_discloses_the_partial_capture(self, monkeypatch):
        # An assembly bigger than the cap gets a PARTIAL snapshot. Reporting that as "all
        # occurrence visibility saved" is the lie: restore cannot put back what was never saved.
        monkeypatch.setattr(iv, "_MAX_OCC", 3)
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}") for i in range(5)]
        _install(monkeypatch, occs, doc_name="Huge")
        iv._SNAPSHOTS.clear()
        out = _payload(iv.handler(action="snapshot"))
        assert out["truncated"] is True and out["occurrence_cap"] == 3
        assert out["occurrences_saved"] == 3
        assert "PARTIAL" in out["note"]

    def test_a_restore_from_a_partial_snapshot_says_so(self, monkeypatch):
        monkeypatch.setattr(iv, "_MAX_OCC", 3)
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}", bulb=True) for i in range(5)]
        _install(monkeypatch, occs, doc_name="HugeRestore")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        for o in occs:
            o.isLightBulbOn = False
        out = _payload(iv.handler(action="restore"))
        assert out["truncated"] is True and out["occurrence_cap"] == 3
        assert out["restored_occurrences"] == 3
        assert "PARTIAL" in out["note"]
        assert [o.isLightBulbOn for o in occs] == [True, True, True, False, False]

    def test_a_partial_snapshot_stays_partial_when_the_design_later_fits_under_the_cap(
            self, monkeypatch):
        # The snapshot is what was CAPTURED; the restore walk only says what is reachable NOW.
        # If the design shrinks under the cap between the two calls the walk reads complete, so
        # only the stored flag still knows the capture was partial - reading the walk alone
        # reports a full restore of a state that was never fully saved.
        monkeypatch.setattr(iv, "_MAX_OCC", 3)
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}", bulb=True) for i in range(5)]
        _install(monkeypatch, occs, doc_name="Shrinker")
        iv._SNAPSHOTS.clear()
        snap = _payload(iv.handler(action="snapshot"))
        assert snap["truncated"] is True
        # two occurrences deleted after the snapshot - the walk now fits under the cap
        iv.app.activeProduct.rootComponent.allOccurrences = occs[:3]
        out = _payload(iv.handler(action="restore"))
        assert out["truncated"] is True and out["occurrence_cap"] == 3
        assert out["restored_occurrences"] == 3
        assert "PARTIAL" in out["note"]

    def test_a_complete_snapshot_restored_into_a_grown_design_discloses_the_capped_walk(
            self, monkeypatch):
        # The mirror case: the CAPTURE was complete, but the design grew past the cap before the
        # restore, so the restore only LOOKED at the first cap occurrences. The stored flag says
        # nothing here - only the current walk knows the restore pass was partial.
        monkeypatch.setattr(iv, "_MAX_OCC", 3)
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}", bulb=True) for i in range(2)]
        _install(monkeypatch, occs, doc_name="Grower")
        iv._SNAPSHOTS.clear()
        snap = _payload(iv.handler(action="snapshot"))
        assert "truncated" not in snap                  # the capture itself was complete
        grown = occs + [FakeOcc(f"N{i}", full_path=f"N{i}") for i in range(4)]
        iv.app.activeProduct.rootComponent.allOccurrences = grown
        out = _payload(iv.handler(action="restore"))
        assert out["truncated"] is True and out["occurrence_cap"] == 3
        assert "PARTIAL" in out["note"]

    def test_a_full_restore_keeps_the_plain_success_note(self, monkeypatch):
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}", bulb=True) for i in range(2)]
        _install(monkeypatch, occs, doc_name="FullRestore")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        out = _payload(iv.handler(action="restore"))
        assert "truncated" not in out and "PARTIAL" not in out["note"]

    def test_clear_isolation_past_the_cap_discloses_what_it_did_not_check(self, monkeypatch):
        monkeypatch.setattr(iv, "_MAX_OCC", 2)
        occs = [FakeOcc(f"O{i}", full_path=f"O{i}", isolated=True) for i in range(4)]
        _install(monkeypatch, occs)
        out = _payload(iv.handler(action="clear_isolation"))
        assert out["cleared_count"] == 2 and out["truncated"] is True
        assert [o.isIsolated for o in occs] == [False, False, True, True]

    def test_clear_isolation_under_the_cap_claims_nothing_extra(self, monkeypatch):
        occs = [FakeOcc("A", full_path="A", isolated=True)]
        _install(monkeypatch, occs)
        out = _payload(iv.handler(action="clear_isolation"))
        assert out["cleared_count"] == 1 and "truncated" not in out

    def test_same_named_documents_do_not_collide(self, monkeypatch):
        # _SNAPSHOTS is keyed by document id, not name: two open documents that happen to share a
        # name (e.g. two "Untitled") must not clobber each other's saved state. A snapshot saved
        # under one doc id must NOT be visible/restorable under another doc that shares the same
        # NAME but has a different id.
        a = FakeOcc("A", full_path="A", bulb=True)
        _install(monkeypatch, [a], doc_name="Untitled", doc_id="urn:doc-one")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        assert "urn:doc-one" in iv._SNAPSHOTS
        # switch to a DIFFERENT document that happens to share the same NAME
        b = FakeOcc("B", full_path="B", bulb=True)
        _install(monkeypatch, [b], doc_name="Untitled", doc_id="urn:doc-two")
        res = iv.handler(action="restore")
        assert res["isError"] is True and "No snapshot saved" in res["message"]
        # the first document's snapshot is untouched
        assert "urn:doc-one" in iv._SNAPSHOTS


# ── request tracer: a per-response 'request_echo' (monotonic seq + the args the handler received) so ──
# ── a REPLAYED response (an identical-replay failure) is diagnosable next time. ─────────────────────

class TestRequestTracer:
    def test_trace_advances_seq_and_echoes_received_args(self):
        t1 = iv._trace("orient", None, "front", "", "", "")
        t2 = iv._trace("hide", "Gear:1", "", "", "", "")
        assert t2["seq"] == t1["seq"] + 1                       # monotonic - a repeat means a replay
        assert t1["received"] == {"action": "orient", "orientation": "front"}
        assert t2["received"] == {"action": "hide", "target": "Gear:1"}

    def test_with_trace_injects_into_ok_payload(self):
        res = iv.ok({"action": "orient", "note": "aimed"})
        out = iv._with_trace(res, {"seq": 7, "received": {"action": "orient"}})
        payload = json.loads(out["content"][0]["text"])
        assert payload["request_echo"] == {"seq": 7, "received": {"action": "orient"}}
        assert payload["note"] == "aimed"                      # original payload preserved

    def test_with_trace_is_noop_on_error(self):
        err = iv.error("boom")
        out = iv._with_trace(err, {"seq": 1, "received": {}})
        assert out is err and out["isError"] is True

    def test_handler_stamps_the_echo(self, monkeypatch):
        # end-to-end: a successful view_set call carries request_echo reflecting its action
        monkeypatch.setattr(iv._common, "design", lambda: object())
        monkeypatch.setattr(iv, "_do_list_views", lambda design: iv.ok({"action": "list_views"}))
        out = json.loads(iv.handler(action="list_views")["content"][0]["text"])
        assert out["request_echo"]["received"]["action"] == "list_views"
