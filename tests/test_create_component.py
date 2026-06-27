"""Unit tests for ``create_component.py`` — make a new empty component occurrence.

Tests written BEFORE the tool is wired (project rule). The logic pinned, no live
Fusion: an empty component+occurrence is created (Occurrences.addNewComponent),
optionally named and/or placed at x/y/z (a translation transform, scaled to cm),
and optionally activated as the edit target. This is the prerequisite for
modelling separate, jointable parts in an assembly.
"""

import json

from conftest import load_tool

cc = load_tool("create_component")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeComponent:
    def __init__(self):
        self.name = "Component1"


class FakeOcc:
    def __init__(self):
        self.name = "Component1:1"
        self.component = FakeComponent()
        self.activated = False

    def activate(self):
        self.activated = True
        return True


class FakeMatrix:
    def __init__(self):
        self.translation = None


class FakeOccurrences:
    def __init__(self):
        self.last_transform = None
        self.count = 0

    def addNewComponent(self, transform):
        self.last_transform = transform
        self.count += 1
        return FakeOcc()


class FakeRoot:
    def __init__(self):
        self.occurrences = FakeOccurrences()


class FakeDesign:
    def __init__(self):
        self.rootComponent = FakeRoot()


def _install():
    design = FakeDesign()
    cc.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.Matrix3D.create = staticmethod(FakeMatrix)
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── behaviour ────────────────────────────────────────────────────────────────

class TestCreateComponent:
    def test_creates_component(self):
        design = _install()
        out = _payload(cc.handler())
        assert out["created"] is True
        assert design.rootComponent.occurrences.count == 1

    def test_names_the_component(self):
        _install()
        out = _payload(cc.handler(name="Mast"))
        # the handler renames component to 'Mast'; reports it
        assert out["component"] == "Mast"

    def test_placed_at_position_scales_to_cm(self):
        design = _install()
        _payload(cc.handler(x=10, y=0, z=5, units="mm"))
        # translation handed to the matrix is in cm (10mm -> 1cm)
        t = design.rootComponent.occurrences.last_transform.translation
        assert t is not None and abs(t[1] - 1.0) < 1e-9 and abs(t[3] - 0.5) < 1e-9

    def test_no_position_uses_identity(self):
        design = _install()
        _payload(cc.handler())
        # no x/y/z -> no translation set on the matrix
        assert design.rootComponent.occurrences.last_transform.translation is None

    def test_activate_makes_it_edit_target(self):
        _install()
        out = _payload(cc.handler(name="Boom", activate=True))
        assert out["activated"] is True

    def test_no_activate_by_default(self):
        _install()
        out = _payload(cc.handler(name="Boom"))
        assert out["activated"] is False

    def test_unknown_units_errors(self):
        _install()
        res = cc.handler(x=5, units="furlongs")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_no_active_design_errors(self):
        cc.app = type("A", (), {"activeProduct": None})()
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = cc.handler()
        assert res["isError"] is True and "No active design" in res["message"]
