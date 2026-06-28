"""Unit tests for ``joint_edit`` — edit an EXISTING joint in place (no remaking).

Tests written BEFORE the handler is wired (project rule). The behaviour pinned,
no live Fusion:

  - find the joint by name; error if absent.
  - the API requires the timeline marker be rolled to BEFORE the joint
    (joint.timelineObject.rollTo(True)) before any property edit, and rolled back
    after — the handler must do this around every edit.
  - selective edits: re-select snap inputs (geometryOrOriginOne/Two via the same
    resolver the joint tool uses), change motion type/axis (setAs<Type>JointMotion),
    toggle isFlipped, set rotationValue (degrees -> radians) for revolute.
  - a no-op call (nothing to change) is reported as an error, not a silent rollTo.

The snap-input resolution is shared with joint.py and exercised live; here the
fakes accept opaque tokens so we pin the orchestration (rollTo, which setters
fire, value conversion) deterministically.
"""

import json
import math

from conftest import load_tool

jt = load_tool("joint")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeTimelineObject:
    def __init__(self):
        self.rolls = []

    def rollTo(self, rollBefore):
        self.rolls.append(bool(rollBefore))
        return True


class FakeLimits:
    def __init__(self):
        self.isMinimumValueEnabled = False
        self.isMaximumValueEnabled = False
        self.isRestValueEnabled = False
        self.minimumValue = None
        self.maximumValue = None
        self.restValue = None


class RevoluteJointMotion:
    """Named to match the real adsk class so _current_joint_type maps it to 'revolute'."""
    def __init__(self):
        self.rotationValue = 0.0
        self.rotationLimits = FakeLimits()


class SliderJointMotion:
    """Linear motion: limits live on slideLimits, values in CENTIMETERS."""
    def __init__(self):
        self.slideValue = 0.0
        self.slideLimits = FakeLimits()


class FakeModelParameter:
    """Matches Joint.offset / Joint.angle — a ModelParameter with settable expression/value."""
    def __init__(self):
        self.expression = None
        self.value = None


class FakeJoint:
    def __init__(self, name):
        self.name = name
        self.timelineObject = FakeTimelineObject()
        self.geometryOrOriginOne = "OLD1"
        self.geometryOrOriginTwo = "OLD2"
        self.isFlipped = False
        self.jointMotion = RevoluteJointMotion()
        self.offset = FakeModelParameter()
        self.angle = FakeModelParameter()
        self.motion_calls = []

    # motion setters record what was requested (custom world axis entity is optional 2nd arg)
    def setAsRevoluteJointMotion(self, axis, custom=None):
        self.motion_calls.append(("revolute", axis, custom))
        return True

    def setAsSliderJointMotion(self, axis, custom=None):
        self.motion_calls.append(("slider", axis, custom))
        return True

    def setAsRigidJointMotion(self):
        self.motion_calls.append(("rigid", None, None))
        return True

    def setAsCylindricalJointMotion(self, axis, custom=None):
        self.motion_calls.append(("cylindrical", axis, custom))
        return True

    def setAsPlanarJointMotion(self, axis, custom=None):
        self.motion_calls.append(("planar", axis, custom))
        return True

    def setAsBallJointMotion(self, a, b):
        self.motion_calls.append(("ball", (a, b), None))
        return True


class FakeJoints:
    def __init__(self, joints):
        self._j = list(joints)

    @property
    def count(self):
        return len(self._j)

    def item(self, i):
        return self._j[i]

    def itemByName(self, name):
        for j in self._j:
            if j.name == name:
                return j
        return None


class FakeRoot:
    def __init__(self, joints):
        self.joints = FakeJoints(joints)
        self.allOccurrences = []
        self.xConstructionAxis = "WAXIS_X"
        self.yConstructionAxis = "WAXIS_Y"
        self.zConstructionAxis = "WAXIS_Z"


class FakeDesign:
    def __init__(self, joints):
        self.rootComponent = FakeRoot(joints)


def _install(joint_names=("BoomPivot",), motion="revolute"):
    joints = [FakeJoint(n) for n in joint_names]
    if motion == "slider":
        for j in joints:
            j.jointMotion = SliderJointMotion()
    design = FakeDesign(joints)
    jt.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # JointDirections axis enum
    JD = adsk.fusion.JointDirections
    JD.XAxisJointDirection = 0
    JD.YAxisJointDirection = 1
    JD.ZAxisJointDirection = 2
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    return design, joints[0]


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── find / guards ────────────────────────────────────────────────────────────

class TestFindAndGuards:
    def test_unknown_joint_errors(self):
        _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="Nope", flip=True)
        assert res["isError"] is True and "No joint named 'Nope'" in res["message"]

    def test_no_edits_requested_errors(self):
        _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot")
        assert res["isError"] is True
        assert "nothing to change" in res["message"].lower()


# ── rollTo orchestration ─────────────────────────────────────────────────────

class TestRollTo:
    def test_rolls_before_then_after(self):
        _, joint = _install(["BoomPivot"])
        _payload(jt.edit_handler(joint_name="BoomPivot", flip=True))
        # first rollTo(True) before editing, then rollTo(False) to restore
        assert joint.timelineObject.rolls[0] is True
        assert joint.timelineObject.rolls[-1] is False


# ── flip ─────────────────────────────────────────────────────────────────────

class TestFlip:
    def test_set_flip(self):
        _, joint = _install(["BoomPivot"])
        out = _payload(jt.edit_handler(joint_name="BoomPivot", flip=True))
        assert joint.isFlipped is True
        assert out["flipped"] is True

    def test_unset_flip(self):
        _, joint = _install(["BoomPivot"])
        joint.isFlipped = True
        _payload(jt.edit_handler(joint_name="BoomPivot", flip=False))
        assert joint.isFlipped is False


# ── motion type / axis ───────────────────────────────────────────────────────

class TestMotion:
    def test_change_to_slider_on_axis(self):
        _, joint = _install(["BoomPivot"])
        _payload(jt.edit_handler(joint_name="BoomPivot", joint_type="slider", axis="x"))
        # frame-relative axis (no custom world entity)
        assert ("slider", 0, None) in joint.motion_calls   # XAxisJointDirection == 0

    def test_change_to_rigid(self):
        _, joint = _install(["BoomPivot"])
        _payload(jt.edit_handler(joint_name="BoomPivot", joint_type="rigid"))
        assert ("rigid", None, None) in joint.motion_calls

    def test_unknown_joint_type_errors(self):
        _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot", joint_type="weld")
        assert res["isError"] is True and "Unknown joint_type" in res["message"]


# ── offset / angle (parameter inputs — full parity with the create joint tool) ──

class TestOffsetAngle:
    def test_offset_sets_expression_with_units(self):
        _, joint = _install(["BoomPivot"])
        out = _payload(jt.edit_handler(joint_name="BoomPivot", offset=-200, units="mm"))
        # offset is a ModelParameter -> set via an explicit-units expression
        assert joint.offset.expression == "-200 mm"
        assert out["offset"] == -200

    def test_offset_inch_units(self):
        _, joint = _install(["BoomPivot"])
        _payload(jt.edit_handler(joint_name="BoomPivot", offset=2, units="in"))
        assert joint.offset.expression == "2 in"

    def test_angle_sets_degrees_expression(self):
        _, joint = _install(["BoomPivot"])
        out = _payload(jt.edit_handler(joint_name="BoomPivot", angle=30))
        assert joint.angle.expression == "30 deg"
        assert out["angle"] == 30

    def test_offset_counts_as_an_edit(self):
        # offset alone should NOT trip the "nothing to change" guard.
        _, joint = _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot", offset=5)
        assert res["isError"] is False

    def test_unknown_units_errors(self):
        _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot", offset=5, units="furlongs")
        assert res["isError"] is True and "Unknown units" in res["message"]


# ── joint limits: rotation (deg/rad) AND linear (mm/cm) + rest ──────────────

import math


class TestLimits:
    def test_rotation_limits_enable_and_set_radians(self):
        _, joint = _install(["BoomPivot"], motion="revolute")
        out = _payload(jt.edit_handler(joint_name="BoomPivot", min_deg=-45, max_deg=90))
        lim = joint.jointMotion.rotationLimits
        assert lim.isMinimumValueEnabled is True and lim.isMaximumValueEnabled is True
        assert abs(lim.minimumValue - math.radians(-45)) < 1e-9
        assert abs(lim.maximumValue - math.radians(90)) < 1e-9
        assert out["min_deg"] == -45 and out["max_deg"] == 90

    def test_rotation_rest_value(self):
        _, joint = _install(["BoomPivot"], motion="revolute")
        _payload(jt.edit_handler(joint_name="BoomPivot", rest_deg=10))
        lim = joint.jointMotion.rotationLimits
        assert lim.isRestValueEnabled is True
        assert abs(lim.restValue - math.radians(10)) < 1e-9

    def test_linear_limits_on_slider_in_cm(self):
        _, joint = _install(["CableSlide"], motion="slider")
        out = _payload(jt.edit_handler(joint_name="CableSlide", min_mm=0, max_mm=300, units="mm"))
        lim = joint.jointMotion.slideLimits
        assert lim.isMaximumValueEnabled is True
        assert abs(lim.maximumValue - 30.0) < 1e-9     # 300 mm -> 30 cm
        assert out["max_mm"] == 300

    def test_linear_rest_value(self):
        _, joint = _install(["CableSlide"], motion="slider")
        _payload(jt.edit_handler(joint_name="CableSlide", rest_mm=50, units="mm"))
        lim = joint.jointMotion.slideLimits
        assert lim.isRestValueEnabled is True
        assert abs(lim.restValue - 5.0) < 1e-9          # 50 mm -> 5 cm

    def test_rotation_limit_on_slider_errors(self):
        # min_deg on a slider (no rotationLimits) should error, not silently no-op.
        _install(["CableSlide"], motion="slider")
        res = jt.edit_handler(joint_name="CableSlide", min_deg=10)
        assert res["isError"] is True
        assert "rotation" in res["message"].lower()

    def test_linear_limit_on_revolute_errors(self):
        _install(["BoomPivot"], motion="revolute")
        res = jt.edit_handler(joint_name="BoomPivot", max_mm=100)
        assert res["isError"] is True
        assert "slide" in res["message"].lower() or "linear" in res["message"].lower()


# ── world_axis: re-point to a TRUE world axis (the boom-about-Y fix) ─────────

class TestWorldAxis:
    def test_world_axis_uses_custom_construction_axis(self):
        _, joint = _install(["BoomPivot"])
        # joint's current motion is revolute (FakeRevoluteMotion); world_axis=z should re-apply
        # revolute with CustomJointDirection + the root's zConstructionAxis ('WAXIS_Z').
        import adsk.fusion
        adsk.fusion.JointDirections.CustomJointDirection = 3
        _payload(jt.edit_handler(joint_name="BoomPivot", world_axis="z"))
        # the recorded call carries the custom world axis entity, not a frame-relative enum
        kinds = [c for c in joint.motion_calls if c[0] == "revolute"]
        assert kinds, "revolute motion should have been re-applied"
        assert kinds[-1][2] == "WAXIS_Z"     # custom entity = world Z construction axis

    def test_world_axis_without_type_reuses_current(self):
        _, joint = _install(["BoomPivot"])
        import adsk.fusion
        adsk.fusion.JointDirections.CustomJointDirection = 3
        out = _payload(jt.edit_handler(joint_name="BoomPivot", world_axis="y"))
        assert out["world_axis"] == "y"
        assert out["joint_type"] == "revolute"   # reused the joint's current type

    def test_unknown_world_axis_errors(self):
        _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot", world_axis="q")
        assert res["isError"] is True and "Unknown world_axis" in res["message"]


# ── rotation drive is refused (unsafe: closes the server connection) ────────

class TestRotationDriveRefused:
    def test_rotation_deg_is_refused_with_redirect(self):
        # Driving jointMotion.rotationValue from this context drops the connection
        # (reproduced live). The tool must refuse and redirect, not attempt it.
        _, joint = _install(["BoomPivot"])
        res = jt.edit_handler(joint_name="BoomPivot", rotation_deg=90)
        assert res["isError"] is True
        assert "assembly_move" in res["message"]
        # and it must NOT have driven the value
        assert joint.jointMotion.rotationValue == 0.0


# ── re-select snap inputs ────────────────────────────────────────────────────

class TestReselectInputs:
    def test_reselect_joint_origin_name_inputs(self):
        # When input_one/input_two resolve (here as JO names via a stubbed resolver),
        # they are assigned to geometryOrOriginOne/Two.
        design, joint = _install(["BoomPivot"])

        # add named joint origins the resolver can find
        class _JO:
            def __init__(self, name): self.name = name
        class _JOs:
            def __init__(self, items): self._i = items
            def itemByName(self, n):
                for j in self._i:
                    if j.name == n:
                        return j
                return None
        design.rootComponent.jointOrigins = _JOs([_JO("A"), _JO("B")])

        out = _payload(jt.edit_handler(joint_name="BoomPivot", input_one="A", input_two="B"))
        assert joint.geometryOrOriginOne.name == "A"
        assert joint.geometryOrOriginTwo.name == "B"
        assert out["input_one"] == "A" and out["input_two"] == "B"
