"""Unit tests for ``view_screenshot.py`` _isolate_for_fit - the fit_to visibility helper.

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
        restore, err = gs._isolate_for_fit("B:1")
        assert restore is not None and err is None
        # only B stays on
        assert b.isLightBulbOn is True
        assert a.isLightBulbOn is False and c.isLightBulbOn is False
        restore()
        assert a.isLightBulbOn is True and c.isLightBulbOn is True

    def test_substring_match(self):
        a = FakeOcc("Bracket:1")
        _install([a, FakeOcc("Other:1")])
        restore, err = gs._isolate_for_fit("bracket")
        assert restore is not None and err is None and a.isLightBulbOn is True

    def test_no_match_returns_none(self):
        _install([FakeOcc("A:1")])
        restore, err = gs._isolate_for_fit("Ghost")
        assert restore is None and err is not None

    def test_ambiguous_name_refused_not_first_match(self):
        # two instances share local name "Bolt:1" under different sub-assemblies - a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT silently isolate the first.
        a = FakeOcc("Bolt:1"); a.fullPathName = "Sub-A:1+Bolt:1"
        b = FakeOcc("Bolt:1"); b.fullPathName = "Sub-B:1+Bolt:1"
        _install([a, b])
        restore, err = gs._isolate_for_fit("Bolt")
        assert restore is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def test_already_hidden_others_not_restored_on(self):
        # an occurrence that was already OFF should stay off after restore (we only flip ones we hid)
        a, b = FakeOcc("A:1", on=True), FakeOcc("B:1", on=False)
        _install([a, b])
        restore, err = gs._isolate_for_fit("A:1")
        restore()
        assert b.isLightBulbOn is False      # we never turned it on


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
