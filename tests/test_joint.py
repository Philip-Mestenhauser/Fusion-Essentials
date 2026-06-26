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
