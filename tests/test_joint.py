"""Unit tests for ``joint.py`` pure logic.

Targets: ``_find_joint_origin`` (resolution order — root JO returned as-is,
empty name -> None, not-found -> None) and ``_apply_motion`` (dispatch by joint
type, including the unsupported-type fallthrough). The assembly-context-proxy
path for sub-component JOs is integration-only (needs a live occurrence graph),
so it's deliberately left to a Fusion test.
"""

from types import SimpleNamespace

from conftest import load_tool

joint = load_tool("joint")


# ── _find_joint_origin: resolution ─────────────────────────────────────────

class _JOCollection:
    def __init__(self, by_name):
        self._by_name = by_name

    def itemByName(self, name):
        return self._by_name.get(name)


def _design_with_root_jos(**jos):
    root = SimpleNamespace(jointOrigins=_JOCollection(jos))
    return SimpleNamespace(rootComponent=root, allComponents=[])


class TestFindJointOrigin:
    def test_empty_name_returns_none(self):
        design = _design_with_root_jos()
        assert joint._find_joint_origin(design, "") is None
        assert joint._find_joint_origin(design, "   ") is None

    def test_root_jo_returned_directly(self):
        target = SimpleNamespace(name="JO_A")
        design = _design_with_root_jos(JO_A=target)
        assert joint._find_joint_origin(design, "JO_A") is target

    def test_name_is_trimmed_before_lookup(self):
        target = SimpleNamespace(name="JO_A")
        design = _design_with_root_jos(JO_A=target)
        assert joint._find_joint_origin(design, "  JO_A  ") is target

    def test_not_found_anywhere_returns_none(self):
        design = _design_with_root_jos(JO_A=SimpleNamespace(name="JO_A"))
        assert joint._find_joint_origin(design, "JO_missing") is None


# ── _apply_motion: dispatch + fallthrough ──────────────────────────────────

class _JointInput:
    """Records which motion setter was called; each returns True (success)."""
    def __init__(self):
        self.called = None

    def setAsRigidJointMotion(self):
        self.called = "rigid"; return True

    def setAsRevoluteJointMotion(self, ax):
        self.called = ("revolute", ax); return True

    def setAsSliderJointMotion(self, ax):
        self.called = ("slider", ax); return True

    def setAsPlanarJointMotion(self, ax):
        self.called = ("planar", ax); return True

    def setAsCylindricalJointMotion(self, ax):
        self.called = ("cylindrical", ax); return True

    def setAsBallJointMotion(self, a, b):
        self.called = "ball"; return True


class TestApplyMotion:
    def test_rigid(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "rigid", 2)
        assert ok is True and err is None
        assert ji.called == "rigid"

    def test_slider_uses_axis_index(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "slider", 0)  # X axis
        assert ok is True and err is None
        assert ji.called[0] == "slider"

    def test_unsupported_type_reports_error(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "warp_drive", 2)
        assert ok is False
        assert "warp_drive" in err
        assert ji.called is None


# ── _apply_limits: shared by create + edit; rotation(rad) vs linear(cm) ──────

import math as _math


class _Lim:
    def __init__(self):
        self.isMinimumValueEnabled = False
        self.isMaximumValueEnabled = False
        self.isRestValueEnabled = False
        self.minimumValue = None
        self.maximumValue = None
        self.restValue = None


class _RevMotion:
    def __init__(self):
        self.rotationLimits = _Lim()
        self.slideLimits = None   # revolute has no slide limits


class _SlideMotion:
    def __init__(self):
        self.rotationLimits = None
        self.slideLimits = _Lim()


class TestApplyLimits:
    def test_rotation_in_radians(self):
        m = _RevMotion()
        changed, err = joint._apply_limits(m, min_deg=-45, max_deg=90)
        assert err is None
        assert m.rotationLimits.isMinimumValueEnabled and m.rotationLimits.isMaximumValueEnabled
        assert abs(m.rotationLimits.minimumValue - _math.radians(-45)) < 1e-9
        assert abs(m.rotationLimits.maximumValue - _math.radians(90)) < 1e-9
        assert changed["min_deg"] == -45 and changed["max_deg"] == 90

    def test_linear_in_cm(self):
        m = _SlideMotion()
        changed, err = joint._apply_limits(m, min_mm=0, max_mm=300, cm_scale=0.1)
        assert err is None
        assert abs(m.slideLimits.maximumValue - 30.0) < 1e-9   # 300 mm -> 30 cm
        assert changed["max_mm"] == 300

    def test_rest_values(self):
        m = _RevMotion()
        joint._apply_limits(m, rest_deg=10)
        assert m.rotationLimits.isRestValueEnabled
        assert abs(m.rotationLimits.restValue - _math.radians(10)) < 1e-9

    def test_rotation_on_slider_errors(self):
        m = _SlideMotion()
        changed, err = joint._apply_limits(m, min_deg=10)
        assert err is not None and "rotation" in err.lower()

    def test_linear_on_revolute_errors(self):
        m = _RevMotion()
        changed, err = joint._apply_limits(m, max_mm=100)
        assert err is not None and ("slide" in err.lower() or "linear" in err.lower())
