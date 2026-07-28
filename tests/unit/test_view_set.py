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
        self.eye = FakePoint(10, 10, 10)
        self.target = FakePoint(0, 0, 0)
        self.upVector = None
        self.isFitView = False


class FakeViewport:
    def __init__(self):
        self.camera = FakeCamera()
        self.visualStyle = 0

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
