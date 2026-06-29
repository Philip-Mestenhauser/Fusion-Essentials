"""Unit tests for joint_motion_link — couple two joints with a ratio (the Motion Link command).

The live motionLinks.add needs Fusion; the testable logic is the joint name resolution and the
input guards (both names required, distinct, must resolve) plus the ratio plumbing.
"""

import json

from conftest import load_tool

jml = load_tool("joint_motion_link")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeJoint:
    def __init__(self, name):
        self.name = name
        # jointType is the JointMotionTypes ENUM the API actually wants in setMotionData (NOT the
        # JointMotion object). Use a recognisable sentinel so the test can assert it's what's passed.
        self.jointMotion = type("JM", (), {"jointType": f"{name}_TYPE"})()


class FakeJoints:
    def __init__(self, names):
        self._j = [FakeJoint(n) for n in names]
    @property
    def count(self):
        return len(self._j)
    def item(self, i):
        return self._j[i]


class FakeMotionLink:
    name = "MotionLink1"
    def __init__(self):
        self.motion_data = None      # captures the setMotionData call
        self.deleted = False
    def setMotionData(self, m1, v1, m2, v2, reversed_):
        self.motion_data = {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "reversed": reversed_}
        return True
    def deleteMe(self):
        self.deleted = True
        return True


class FakeMotionLinks:
    def __init__(self):
        self.created_with = None     # the (j1, j2) tuple passed to createInput
        self.added = None
        self.last_link = None
    def createInput(self, j1, j2):   # real API: two joints, NOT a collection
        self.created_with = (j1, j2)
        return type("MLI", (), {})()
    def add(self, inp):
        self.added = inp
        self.last_link = FakeMotionLink()
        return self.last_link


class FakeRoot:
    def __init__(self, names):
        self.joints = FakeJoints(names)
        self.motionLinks = FakeMotionLinks()


class FakeDesign:
    def __init__(self, names):
        self.rootComponent = FakeRoot(names)


def _install(joint_names):
    des = FakeDesign(joint_names)
    jml.app = type("A", (), {"activeProduct": des})()
    jml._common.app = jml.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # ValueInput.createByReal echoes the real number it was given so a test can assert the ratio.
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    return des


class TestFindJoint:
    def test_exact_then_case_insensitive(self):
        des = _install(["Wheel_Spin", "Crank1_to_Wheel"])
        root = des.rootComponent
        j, names = jml._find_joint(root, "wheel_spin")
        assert j.name == "Wheel_Spin"
        assert "Crank1_to_Wheel" in names


class TestHandlerGuards:
    def test_requires_both_names(self):
        _install(["A", "B"])
        assert jml.handler(joint_one="A")["isError"] is True
        assert jml.handler(joint_two="B")["isError"] is True

    def test_rejects_same_joint(self):
        _install(["A", "B"])
        res = jml.handler(joint_one="A", joint_two="A")
        assert res["isError"] is True and "different" in res["message"]

    def test_unknown_joint_lists_available(self):
        _install(["Wheel_Spin", "Pedal1_Spin"])
        res = jml.handler(joint_one="Wheel_Spin", joint_two="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "Wheel_Spin" in res["message"]

    def test_no_design(self):
        jml.app = type("A", (), {"activeProduct": None})()
        jml._common.app = jml.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        assert jml.handler(joint_one="A", joint_two="B")["isError"] is True


class TestLinkCreation:
    def test_createInput_gets_two_joints_not_a_collection(self):
        # REGRESSION: the old code called createInput(ObjectCollection) — the real API is
        # createInput(jointOne, jointTwo). Assert the two joints arrive as separate args.
        des = _install(["Wheel_Spin", "Crank1_to_Wheel"])
        _payload(jml.handler(joint_one="Wheel_Spin", joint_two="Crank1_to_Wheel", ratio=2.0))
        j1, j2 = des.rootComponent.motionLinks.created_with
        assert j1.name == "Wheel_Spin" and j2.name == "Crank1_to_Wheel"

    def test_ratio_flows_through_setMotionData(self):
        # REGRESSION: the old code set inp.ratios (nonexistent, swallowed) so every link was 1:1.
        # The ratio must reach MotionLink.setMotionData as valueOne=1, valueTwo=|ratio|.
        des = _install(["Wheel_Spin", "Crank1_to_Wheel"])
        out = _payload(jml.handler(joint_one="Wheel_Spin", joint_two="Crank1_to_Wheel", ratio=2.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md is not None, "setMotionData was never called — ratio is a no-op"
        assert md["v1"] == ("real", 1.0)
        assert md["v2"] == ("real", 2.0)      # valueTwo carries the ratio magnitude
        assert md["reversed"] is False
        # motionOne/Two must be the jointType ENUM, not the JointMotion object (passing the object
        # raises "Wrong number or type of arguments").
        assert md["m1"] == "Wheel_Spin_TYPE"
        assert md["m2"] == "Crank1_to_Wheel_TYPE"
        assert out["ratio"] == 2.0 and out["ratio_applied"] is True

    def test_default_ratio_is_one(self):
        des = _install(["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B"))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v1"] == ("real", 1.0) and md["v2"] == ("real", 1.0)
        assert out["ratio"] == 1.0

    def test_negative_ratio_links_reversed_with_magnitude(self):
        des = _install(["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=-3.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v2"] == ("real", 3.0)      # magnitude only
        assert md["reversed"] is True
        assert out["reversed"] is True

    def test_zero_ratio_rejected(self):
        _install(["A", "B"])
        res = jml.handler(joint_one="A", joint_two="B", ratio=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_non_numeric_ratio_rejected(self):
        # a ratio that won't float() must error cleanly (not crash), before any link is created.
        des = _install(["A", "B"])
        res = jml.handler(joint_one="A", joint_two="B", ratio="banana")
        assert res["isError"] is True and "must be a number" in res["message"]
        # and no link was ever added
        assert des.rootComponent.motionLinks.added is None

    def test_numeric_string_ratio_accepted(self):
        # "2" is a valid number string -> float() succeeds, magnitude reaches setMotionData.
        des = _install(["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio="2"))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v2"] == ("real", 2.0)
        assert out["ratio"] == 2.0

    def test_ratio_failure_rolls_back_link_and_errors(self):
        # If setMotionData fails (e.g. BAD_JOINT_DOF), the just-added link is a compute-failed
        # feature — the tool must DELETE it and return an error, NOT leave a broken 1:1 link or
        # claim success.
        des = _install(["A", "B"])
        des.rootComponent.motionLinks.add = (
            lambda inp: _link_that_raises(des.rootComponent.motionLinks))
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "could not apply the ratio" in res["message"]
        assert des.rootComponent.motionLinks.last_link.deleted is True   # rolled back

    def test_bad_joint_dof_gets_actionable_hint(self):
        des = _install(["A", "B"])
        des.rootComponent.motionLinks.add = (
            lambda inp: _link_that_raises(des.rootComponent.motionLinks,
                                          "Compute Failed // BAD_JOINT_DOF - wrong type"))
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "independent" in res["message"]      # explains the same-chain cause


def _link_that_raises(mls, msg="joint motion type cannot be linked"):
    link = FakeMotionLink()
    def boom(*a, **k):
        raise RuntimeError(msg)
    link.setMotionData = boom
    mls.last_link = link
    return link
