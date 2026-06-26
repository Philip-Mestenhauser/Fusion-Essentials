"""Unit tests for ``inspect_view.py`` — the agent's view verbs.

Camera math (eye/target/up per orientation) is a live-viewport side-effect best
left to a real session, but the surrounding LOGIC is pure and worth pinning:
action validation, occurrence resolution (exact beats substring; isolate needs
exactly one match), the visibility verbs (isolate flag, hide bulb,
clear_isolation, show lighting the whole ancestor chain), style validation, the
named-view library (save overwrites same name, apply errors with a list, list
reports built-ins), and the snapshot -> mutate -> restore round-trip that puts
bulbs/isolation/style back. Fakes expose the real attributes the tool sets so we
can assert on them.
"""

import json

from conftest import load_tool

iv = load_tool("inspect_view")


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
    def __init__(self, occurrences):
        self.allOccurrences = list(occurrences)


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
    def __init__(self, occurrences, named_views=None):
        self.rootComponent = FakeRoot(occurrences)
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


class FakeDoc:
    def __init__(self, name):
        self.name = name


class FakeApp:
    def __init__(self, design, doc_name="Doc"):
        self.activeProduct = design
        self.activeDocument = FakeDoc(doc_name)
        self.activeViewport = FakeViewport()


def _install(occurrences=(), named_views=None, doc_name="Doc"):
    design = FakeDesign(list(occurrences), named_views)
    iv.app = FakeApp(design, doc_name)
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # adsk.core.VisualStyles.<Name> must resolve to an int for _do_style.
    import adsk.core
    vs = adsk.core.VisualStyles
    for i, attr in enumerate(["ShadedVisualStyle", "WireframeVisualStyle",
                              "ShadedWithVisibleEdgesOnlyVisualStyle",
                              "ShadedWithHiddenEdgesVisualStyle",
                              "WireframeWithHiddenEdgesVisualStyle",
                              "WireframeWithVisibleEdgesOnlyVisualStyle"]):
        setattr(vs, attr, i + 1)
    # Point3D/Vector3D create -> simple carriers (orient math touches these).
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: FakePoint(x, y, z))
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: FakePoint(x, y, z))
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self):
        _install()
        res = iv.handler(action="zoomzoom")
        assert res["isError"] is True and "Unknown action" in res["message"]

    def test_no_design(self):
        iv.app = FakeApp(None)
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = iv.handler(action="orient", orientation="front")
        assert res["isError"] is True and "No active design" in res["message"]


# ── occurrence resolution via visibility verbs ──────────────────────────────

class TestVisibility:
    def test_hide_turns_bulb_off(self):
        occ = FakeOcc("Bracket", bulb=True)
        _install([occ])
        _payload(iv.handler(action="hide", target="Bracket"))
        assert occ.isLightBulbOn is False

    def test_isolate_sets_flag(self):
        occ = FakeOcc("Bracket")
        _install([occ])
        _payload(iv.handler(action="isolate", target="Bracket"))
        assert occ.isIsolated is True

    def test_isolate_requires_single_match(self):
        a = FakeOcc("Bolt:1")
        b = FakeOcc("Bolt:2")
        _install([a, b])
        res = iv.handler(action="isolate", target="Bolt")
        assert res["isError"] is True
        assert "needs exactly one" in res["message"]

    def test_exact_name_beats_substring(self):
        exact = FakeOcc("Bolt")
        longer = FakeOcc("Bolt Flange")
        _install([longer, exact])
        # 'Bolt' exact-matches one, substring-matches both; exact wins -> single isolate ok
        out = _payload(iv.handler(action="isolate", target="Bolt"))
        assert out["affected"] == ["Bolt"]
        assert exact.isIsolated is True
        assert longer.isIsolated is False

    def test_show_lights_ancestor_chain(self):
        parent = FakeOcc("Assembly", bulb=False)
        child = FakeOcc("Screw", full_path="Assembly+Screw", parent=parent, bulb=False)
        _install([parent, child])
        out = _payload(iv.handler(action="show", target="Screw"))
        assert child.isLightBulbOn is True
        assert parent.isLightBulbOn is True          # ancestor lit too
        assert "Assembly" in out["ancestors_also_shown"]

    def test_clear_isolation_resets_all(self):
        a = FakeOcc("A", isolated=True)
        b = FakeOcc("B", isolated=True)
        _install([a, b])
        out = _payload(iv.handler(action="clear_isolation"))
        assert out["cleared_count"] == 2
        assert a.isIsolated is False and b.isIsolated is False

    def test_unmatched_target_errors(self):
        _install([FakeOcc("A")])
        res = iv.handler(action="hide", target="Ghost")
        assert res["isError"] is True and "No occurrence matched" in res["message"]

    def test_missing_target_errors(self):
        _install([FakeOcc("A")])
        res = iv.handler(action="hide")
        assert res["isError"] is True and "Provide 'target'" in res["message"]


# ── style ────────────────────────────────────────────────────────────────────

class TestStyle:
    def test_wireframe_sets_visual_style(self):
        _install()
        out = _payload(iv.handler(action="style", style="wireframe"))
        assert out["style"] == "wireframe"
        assert out["visual_style_after"] == iv.app.activeViewport.visualStyle

    def test_unknown_style_errors(self):
        _install()
        res = iv.handler(action="style", style="crayon")
        assert res["isError"] is True and "Provide 'style'" in res["message"]


# ── orient ───────────────────────────────────────────────────────────────────

class TestOrient:
    def test_unknown_orientation_errors(self):
        _install([FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        res = iv.handler(action="orient", orientation="sideways")
        assert res["isError"] is True and "Unknown orientation" in res["message"]

    def test_focus_unknown_occurrence_errors(self):
        _install([FakeOcc("Part")])
        res = iv.handler(action="orient", orientation="front", focus="Ghost")
        assert res["isError"] is True and "No occurrence matched focus" in res["message"]

    def test_front_orientation_sets_up_vector(self):
        _install([FakeOcc("Part", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))])
        out = _payload(iv.handler(action="orient", orientation="front", focus="Part"))
        assert out["applied"]["orientation"] == "front"
        assert out["applied"]["focus"] == "Part"
        # front up vector is +Z per _ORIENTATIONS
        up = iv.app.activeViewport.camera.upVector
        assert (up.x, up.y, up.z) == (0, 0, 1)


# ── named views ──────────────────────────────────────────────────────────────

class TestNamedViews:
    def test_save_view_adds(self):
        nvs = FakeNamedViews()
        _install([], named_views=nvs)
        out = _payload(iv.handler(action="save_view", view_name="MyAngle"))
        assert out["view_name"] == "MyAngle"
        assert nvs.count == 1

    def test_save_view_overwrites_same_name(self):
        old = FakeNamedView("MyAngle")
        nvs = FakeNamedViews([old])
        _install([], named_views=nvs)
        _payload(iv.handler(action="save_view", view_name="MyAngle"))
        assert old._deleted is True          # old one removed before re-add
        assert nvs.count == 1                 # still just one "MyAngle"

    def test_save_view_requires_name(self):
        _install([], named_views=FakeNamedViews())
        res = iv.handler(action="save_view")
        assert res["isError"] is True and "Provide 'view_name'" in res["message"]

    def test_apply_view_moves_camera(self):
        nv = FakeNamedView("Home", built_in=True)
        _install([], named_views=FakeNamedViews([nv]))
        _payload(iv.handler(action="apply_view", view_name="Home"))
        assert nv.applied is True

    def test_apply_unknown_view_lists_available(self):
        _install([], named_views=FakeNamedViews([FakeNamedView("Home")]))
        res = iv.handler(action="apply_view", view_name="Nope")
        assert res["isError"] is True
        assert "No named view 'Nope'" in res["message"]
        assert "Home" in res["message"]

    def test_list_views_reports_builtin_flag(self):
        _install([], named_views=FakeNamedViews(
            [FakeNamedView("Home", built_in=True), FakeNamedView("Mine")]))
        out = _payload(iv.handler(action="list_views"))
        assert out["count"] == 2
        by = {v["name"]: v["built_in"] for v in out["named_views"]}
        assert by["Home"] is True and by["Mine"] is False


# ── snapshot / restore round-trip ───────────────────────────────────────────

class TestSnapshotRestore:
    def test_restore_without_snapshot_errors(self):
        _install([FakeOcc("A")], doc_name="FreshDoc")
        iv._SNAPSHOTS.clear()
        res = iv.handler(action="restore")
        assert res["isError"] is True and "No snapshot saved" in res["message"]

    def test_snapshot_then_restore_puts_bulbs_back(self):
        a = FakeOcc("A", full_path="A", bulb=True)
        b = FakeOcc("B", full_path="B", bulb=True)
        _install([a, b], doc_name="RoundTrip")
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

    def test_restore_counts_missing_occurrences(self):
        a = FakeOcc("A", full_path="A")
        _install([a], doc_name="MissingTest")
        iv._SNAPSHOTS.clear()
        _payload(iv.handler(action="snapshot"))
        # remove 'A' from the design before restoring -> it's now missing
        iv.app.activeProduct.rootComponent.allOccurrences = []
        out = _payload(iv.handler(action="restore"))
        assert out["missing_occurrences"] == 1
        assert out["restored_occurrences"] == 0
