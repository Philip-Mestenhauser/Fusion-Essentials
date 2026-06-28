"""Unit tests for ``get_screenshot.py`` _isolate_for_fit — the fit_to visibility helper.

The image capture itself needs a live viewport (integration-tested), but the fit_to helper is pure
visibility bookkeeping: find the named occurrence, hide the others, return a restore() that turns
them back on. That's exactly the bug-prone part (matching + restore), so it gets unit coverage.
"""

from conftest import load_tool

gs = load_tool("get_screenshot")


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
    gs.app = type("A", (), {"activeProduct": design})()
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
