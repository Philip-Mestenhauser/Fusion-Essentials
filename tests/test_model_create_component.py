"""Unit tests for ``model_create_component.py`` — make a new empty component occurrence.

Tests written BEFORE the tool is wired (project rule). The logic pinned, no live
Fusion: an empty component+occurrence is created (Occurrences.addNewComponent),
optionally named and/or placed at x/y/z (a translation transform, scaled to cm),
and optionally activated as the edit target. This is the prerequisite for
modelling separate, jointable parts in an assembly.
"""

import json

from conftest import load_tool

cc = load_tool("model_create_component")


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
        self.rotation = None
    def setToRotation(self, angle, axis, origin):
        self.rotation = (angle, axis, origin)


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
    cc._common.app = cc.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.Matrix3D.create = staticmethod(FakeMatrix)
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
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
        assert "name_warning" not in out          # a successful rename has no warning

    def test_rejected_rename_surfaces_warning_not_false_success(self):
        # Fusion silently no-ops a duplicate/invalid component rename. The handler must read the name
        # back and WARN, not report the requested name as if it took.
        design = _install()

        class _LockedComp:
            name = "Component1"           # class-level: assignment to instance.name still works...
            def __setattr__(self, k, v):
                pass                       # ...but we swallow it -> rename silently fails

        locked = _LockedComp()
        design.rootComponent.occurrences.addNewComponent = lambda t: type(
            "O", (), {"name": "Component1:1", "component": locked, "activate": lambda s: True})()
        out = _payload(cc.handler(name="Mast"))
        assert out["component"] == "Component1"    # the ACTUAL (unchanged) name, honestly
        assert "name_warning" in out and "Mast" in out["name_warning"]

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

    def test_orientation_rotation(self):
        design = _install()
        out = _payload(cc.handler(rotate_deg=90, rotate_axis="x"))
        assert design.rootComponent.occurrences.last_transform.rotation is not None
        assert out["rotate_axis"] == "x" and out["rotate_deg"] == 90

    def test_rotation_angle_converted_to_radians(self):
        import math
        design = _install()
        _payload(cc.handler(rotate_deg=90, rotate_axis="z"))
        angle, axis, origin = design.rootComponent.occurrences.last_transform.rotation
        assert abs(angle - math.pi / 2) < 1e-9        # 90deg -> pi/2 rad
        assert axis == ("vec", 0, 0, 1)               # z axis

    def test_rotation_origin_scaled_to_cm(self):
        design = _install()
        _payload(cc.handler(rotate_deg=45, rotate_axis="y", x=10, y=0, z=20, units="mm"))
        angle, axis, origin = design.rootComponent.occurrences.last_transform.rotation
        # origin is the scaled placement point (10mm -> 1cm, 20mm -> 2cm)
        assert origin == ("pt", 1.0, 0.0, 2.0)

    def test_rotate_axis_none_when_no_rotation(self):
        _install()
        out = _payload(cc.handler(name="A"))
        assert out["rotate_axis"] is None
        assert out["rotate_deg"] == 0.0

    def test_position_scaled_inches(self):
        design = _install()
        _payload(cc.handler(x=1, y=2, z=0, units="in"))
        t = design.rootComponent.occurrences.last_transform.translation
        # 1in -> 2.54cm, 2in -> 5.08cm
        assert abs(t[1] - 2.54) < 1e-9 and abs(t[2] - 5.08) < 1e-9

    def test_unknown_rotate_axis_errors(self):
        _install()
        res = cc.handler(rotate_deg=45, rotate_axis="w")
        assert res["isError"] is True and "rotate_axis" in res["message"]

    def test_no_active_design_errors(self):
        cc.app = type("A", (), {"activeProduct": None})()
        cc._common.app = cc.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = cc.handler()
        assert res["isError"] is True and "No active design" in res["message"]
