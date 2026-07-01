"""Unit tests for ``joint_drive`` — the Drive Joints command (set a joint's value).

Pinned (no live Fusion): the type gate (only revolute/slider/cylindrical drivable; rigid/ball refused),
the angle-vs-distance argument matching (a slider rejects angle_deg; a revolute rejects distance), the
degrees->radians and mm->cm conversions onto the API's value setters, the enabled-limit warning, and
the value read-back. The motion classes are NAMED to match the real adsk classes so the shared
_current_joint_type maps them correctly.
"""

import json
import math

from conftest import load_tool

jd = load_tool("joint_drive")


# ── fakes (class NAMES matter: _current_joint_type keys off type(jm).__name__) ──────────────────────

class FakeLimits:
    def __init__(self, min_on=False, minv=None, max_on=False, maxv=None):
        self.isMinimumValueEnabled = min_on
        self.minimumValue = minv
        self.isMaximumValueEnabled = max_on
        self.maximumValue = maxv


class RevoluteJointMotion:
    def __init__(self):
        self.rotationValue = 0.0
        self.rotationLimits = FakeLimits()


class SliderJointMotion:
    def __init__(self):
        self.slideValue = 0.0            # centimeters
        self.slideLimits = FakeLimits()


class CylindricalJointMotion:
    def __init__(self):
        self.rotationValue = 0.0
        self.slideValue = 0.0
        self.rotationLimits = FakeLimits()
        self.slideLimits = FakeLimits()


class RigidJointMotion:
    pass


class FakeJoint:
    def __init__(self, name, motion):
        self.name = name
        self.jointMotion = motion


class _Joints:
    def __init__(self, joints):
        self._j = list(joints)
    def itemByName(self, name):
        return next((j for j in self._j if j.name == name), None)


class _Root:
    def __init__(self, joints, asbuilt=()):
        self.joints = _Joints(joints)
        self.asBuiltJoints = _Joints(asbuilt)


class _Design:
    def __init__(self, joints, asbuilt=()):
        self.rootComponent = _Root(joints, asbuilt)
        self.allComponents = []


def _install(joint, asbuilt=()):
    design = _Design([joint], asbuilt)
    jd._common.design = lambda: design
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards / type gate ───────────────────────────────────────────────────────

class TestGuards:
    def test_no_value_given_errors(self):
        _install(FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="J")
        assert res["isError"] is True and "drive" in res["message"].lower()

    def test_unknown_units(self):
        _install(FakeJoint("J", SliderJointMotion()))
        res = jd.handler(joint_name="J", distance=5, units="furlong")
        assert res["isError"] is True and "unit" in res["message"].lower()

    def test_joint_not_found(self):
        _install(FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="Ghost", angle_deg=10)
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_as_built_joint_is_drivable_by_name(self):
        # an as-built REVOLUTE (in root.asBuiltJoints, a separate collection) must be found + driven,
        # not reported "no joint named ...". This is the cold-build case (script-created as-built spin).
        spin = FakeJoint("AsBuiltSpin", RevoluteJointMotion())
        _install(FakeJoint("Regular", RigidJointMotion()), asbuilt=[spin])
        out = _payload(jd.handler(joint_name="AsBuiltSpin", angle_deg=90))
        assert out["driven"] is True
        assert abs(spin.jointMotion.rotationValue - math.pi / 2) < 1e-9

    def test_rigid_is_refused(self):
        _install(FakeJoint("J", RigidJointMotion()))
        res = jd.handler(joint_name="J", angle_deg=10)
        assert res["isError"] is True and "revolute" in res["message"].lower()

    def test_slider_rejects_angle(self):
        _install(FakeJoint("J", SliderJointMotion()))
        res = jd.handler(joint_name="J", angle_deg=10)
        assert res["isError"] is True and "distance" in res["message"].lower()

    def test_revolute_rejects_distance(self):
        _install(FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="J", distance=10)
        assert res["isError"] is True and "angle_deg" in res["message"]


# ── revolute drive: degrees -> radians ──────────────────────────────────────

class TestRevoluteDrive:
    def test_angle_set_in_radians(self):
        j = FakeJoint("Pivot", RevoluteJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=90))
        assert abs(j.jointMotion.rotationValue - math.pi / 2) < 1e-9
        assert out["driven"] is True and out["joint_type"] == "revolute"
        assert out["applied"]["angle_deg"] == 90.0

    def test_value_read_back_in_degrees(self):
        j = FakeJoint("Pivot", RevoluteJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=45))
        assert abs(out["value_now"]["angle_deg"] - 45.0) < 1e-4

    def test_over_limit_warns(self):
        m = RevoluteJointMotion()
        m.rotationLimits = FakeLimits(max_on=True, maxv=math.radians(30))   # max 30 deg
        j = FakeJoint("Pivot", m)
        _install(j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=60))        # exceeds 30
        assert "limit_warnings" in out and "maximum" in out["limit_warnings"][0]


# ── slider drive: mm -> cm ──────────────────────────────────────────────────

class TestSliderDrive:
    def test_distance_set_in_cm(self):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert abs(j.jointMotion.slideValue - 5.0) < 1e-9       # 50 mm -> 5 cm
        assert out["joint_type"] == "slider"

    def test_distance_read_back_in_mm(self):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert abs(out["value_now"]["distance_mm"] - 50.0) < 1e-4

    def test_inch_distance(self):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(j)
        jd.handler(joint_name="Rail", distance=1, units="in")
        assert abs(j.jointMotion.slideValue - 2.54) < 1e-9      # 1 in -> 2.54 cm

    def test_below_min_warns(self):
        m = SliderJointMotion()
        m.slideLimits = FakeLimits(min_on=True, minv=0.0)        # min 0 cm
        j = FakeJoint("Rail", m)
        _install(j)
        out = _payload(jd.handler(joint_name="Rail", distance=-20, units="mm"))   # -2 cm < 0
        assert "limit_warnings" in out and "minimum" in out["limit_warnings"][0]


# ── cylindrical drive: both angle + distance ────────────────────────────────

class TestCylindricalDrive:
    def test_drives_both_values(self):
        j = FakeJoint("Cyl", CylindricalJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Cyl", angle_deg=30, distance=10, units="mm"))
        assert abs(j.jointMotion.rotationValue - math.radians(30)) < 1e-9
        assert abs(j.jointMotion.slideValue - 1.0) < 1e-9       # 10 mm -> 1 cm
        assert out["applied"] == {"angle_deg": 30.0, "distance": 10.0}
        assert "angle_deg" in out["value_now"] and "distance_mm" in out["value_now"]

    def test_cylindrical_angle_only(self):
        j = FakeJoint("Cyl", CylindricalJointMotion())
        _install(j)
        out = _payload(jd.handler(joint_name="Cyl", angle_deg=15))
        assert abs(j.jointMotion.rotationValue - math.radians(15)) < 1e-9
        assert j.jointMotion.slideValue == 0.0                  # untouched
