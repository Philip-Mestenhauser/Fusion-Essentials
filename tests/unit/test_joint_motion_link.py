"""Unit tests for joint_motion_link — couple two joints with a ratio (the Motion Link command).

The live motionLinks.add needs Fusion; the testable logic is the joint name resolution and the
input guards (both names required, distinct, must resolve) plus the ratio plumbing.
"""

import json

import adsk.fusion

from conftest import FakeVector3D, load_tool

jml = load_tool("joint_motion_link")

# The DOF values setMotionData actually wants, straight from the measured enum (never hand-typed).
JMT = adsk.fusion.JointMotionTypes
REVOLUTE_DOF = JMT.RevoluteJointRotateMotionType
SLIDER_DOF = JMT.SliderJointSlideMotionType


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeJoint:
    def __init__(self, name, motion="RevoluteJointMotion"):
        self.name = name
        # The JointMotion SUBCLASS name is what motion_link_dof maps to a JointMotionTypes DOF;
        # jointType returns a JointTypes value, which is the WRONG enum for setMotionData - the
        # tool must not pass it. jointType carries a sentinel so a regression that passes it is caught.
        self.jointMotion = type(motion, (), {"jointType": f"{name}_JOINTTYPE"})()


class FakeJoints:
    def __init__(self, joints):
        self._j = list(joints)
    @property
    def count(self):
        return len(self._j)
    def item(self, i):
        return self._j[i]
    def itemByName(self, name):
        return next((j for j in self._j if j.name == name), None)


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
    def __init__(self, names, asbuilt=()):
        self.joints = FakeJoints(FakeJoint(n) for n in names)
        self.asBuiltJoints = FakeJoints(FakeJoint(n) for n in asbuilt)
        self.motionLinks = FakeMotionLinks()


class FakeDesign:
    def __init__(self, names, asbuilt=()):
        self.rootComponent = FakeRoot(names, asbuilt)
        self.allComponents = []


def _install(monkeypatch, joint_names, asbuilt=()):
    des = FakeDesign(joint_names, asbuilt)
    app = type("A", (), {"activeProduct": des})()
    monkeypatch.setattr(jml, "app", app)
    monkeypatch.setattr(jml._common, "app", app)
    import adsk.fusion, adsk.core
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    # ValueInput.createByReal echoes the real number it was given so a test can assert the ratio.
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", staticmethod(lambda v: ("real", v)))
    return des


class TestFindJoint:
    def test_finds_root_joint_by_exact_name(self, monkeypatch):
        des = _install(monkeypatch, ["Wheel_Spin", "Crank1_to_Wheel"])
        j = jml.find_joint(des, "Wheel_Spin")
        assert j.name == "Wheel_Spin"

    def test_finds_as_built_joint(self, monkeypatch):
        # asBuiltJoints is a SEPARATE collection from joints - a joint living only there must still
        # resolve (joint_motion_link must not fork its own root-only lookup).
        des = _install(monkeypatch, ["Wheel_Spin"], asbuilt=["Spin_Link"])
        j = jml.find_joint(des, "Spin_Link")
        assert j is not None and j.name == "Spin_Link"

    def test_unknown_name_returns_none(self, monkeypatch):
        des = _install(monkeypatch, ["Wheel_Spin"])
        assert jml.find_joint(des, "Ghost") is None


class TestAllJoints:
    def _sub(self, joints=(), asbuilt=()):
        return type("Sub", (), {"joints": FakeJoints([FakeJoint(n) for n in joints]),
                                "asBuiltJoints": FakeJoints([FakeJoint(n) for n in asbuilt])})()

    def test_walks_root_and_subcomponents_and_asbuilt(self):
        # a root-only walk would miss Sub_J / Sub_AB - the under-reporting failure mode this guards.
        root = FakeRoot(["Root_A"], asbuilt=["Root_AB"])
        sub = self._sub(joints=["Sub_J"], asbuilt=["Sub_AB"])
        des = type("D", (), {"rootComponent": root, "allComponents": [sub]})()
        names = sorted(j.name for j in jml.all_joints(des))
        assert names == ["Root_A", "Root_AB", "Sub_AB", "Sub_J"]

    def test_dedups_root_when_allcomponents_includes_it(self):
        # the live API lists the root component INSIDE allComponents; all_joints must not count root's
        # joints twice.
        root = FakeRoot(["Root_A"])
        des = type("D", (), {"rootComponent": root, "allComponents": [root]})()
        assert [j.name for j in jml.all_joints(des)] == ["Root_A"]

    def test_dedups_root_reached_via_distinct_proxy(self):
        # the live API returns the root from allComponents as a proxy that is NOT `is`-identical to
        # design.rootComponent, so identity de-dup misses it and the root's joints count twice. The
        # two proxies' joints share an entityToken, so de-dup by token holds across them. (Identity
        # de-dup returned ["Root_A", "Root_A"] here - joint_count must not double-count one joint.)
        def tokened(name, token):
            return type("J", (), {"name": name, "entityToken": token})()
        def root_proxy():
            return type("Root", (), {"joints": FakeJoints([tokened("Root_A", "TOKEN_ROOT_A")]),
                                     "asBuiltJoints": FakeJoints([])})()
        des = type("D", (), {"rootComponent": root_proxy(), "allComponents": [root_proxy()]})()
        assert [j.name for j in jml.all_joints(des)] == ["Root_A"]

    def test_empty_design_is_safe(self):
        des = type("D", (), {"rootComponent": FakeRoot([]), "allComponents": []})()
        assert jml.all_joints(des) == []


class TestHandlerGuards:
    def test_requires_both_names(self, monkeypatch):
        _install(monkeypatch, ["A", "B"])
        res1 = jml.handler(joint_one="A")
        assert res1["isError"] is True
        assert "Provide 'joint_one' and 'joint_two'" in res1["message"]
        res2 = jml.handler(joint_two="B")
        assert res2["isError"] is True
        assert "Provide 'joint_one' and 'joint_two'" in res2["message"]

    def test_rejects_same_joint(self, monkeypatch):
        _install(monkeypatch, ["A", "B"])
        res = jml.handler(joint_one="A", joint_two="A")
        assert res["isError"] is True and "different" in res["message"]

    def test_unknown_joint_lists_available(self, monkeypatch):
        _install(monkeypatch, ["Wheel_Spin", "Pedal1_Spin"])
        res = jml.handler(joint_one="Wheel_Spin", joint_two="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "Wheel_Spin" in res["message"]

    def test_no_design(self, monkeypatch):
        app = type("A", (), {"activeProduct": None})()
        monkeypatch.setattr(jml, "app", app)
        monkeypatch.setattr(jml._common, "app", app)
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: None)
        res = jml.handler(joint_one="A", joint_two="B")
        assert res["isError"] is True
        assert "No active design" in res["message"]


class TestLinkCreation:
    def test_createInput_gets_two_joints_not_a_collection(self, monkeypatch):
        # The real API is createInput(jointOne, jointTwo), not createInput(ObjectCollection).
        # Assert the two joints arrive as separate args.
        des = _install(monkeypatch, ["Wheel_Spin", "Crank1_to_Wheel"])
        _payload(jml.handler(joint_one="Wheel_Spin", joint_two="Crank1_to_Wheel", ratio=2.0))
        j1, j2 = des.rootComponent.motionLinks.created_with
        assert j1.name == "Wheel_Spin" and j2.name == "Crank1_to_Wheel"

    def test_ratio_flows_through_setMotionData(self, monkeypatch):
        # The ratio must reach MotionLink.setMotionData as valueOne=1, valueTwo=|ratio| - writing
        # a nonexistent property like inp.ratios is silently swallowed and leaves every link 1:1.
        des = _install(monkeypatch, ["Wheel_Spin", "Crank1_to_Wheel"])
        out = _payload(jml.handler(joint_one="Wheel_Spin", joint_two="Crank1_to_Wheel", ratio=2.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md is not None, "setMotionData was never called — ratio is a no-op"
        assert md["v1"] == ("real", 1.0)
        assert md["v2"] == ("real", 2.0)      # valueTwo carries the ratio magnitude
        assert md["reversed"] is False
        # motionOne/Two must be the JointMotionTypes DOF (RevoluteJointRotateMotionType for a
        # revolute), NOT the JointTypes value jointMotion.jointType returns - passing that raises
        # "BAD_JOINT_DOF - Motion Link joint DOF is wrong type".
        assert md["m1"] == REVOLUTE_DOF
        assert md["m2"] == REVOLUTE_DOF
        assert md["m1"] != "Wheel_Spin_JOINTTYPE"      # the wrong-enum regression
        assert out["ratio"] == 2.0 and out["ratio_applied"] is True

    def test_slider_maps_to_slide_dof(self, monkeypatch):
        # a slider joint's linkable DOF is SliderJointSlideMotionType, not its jointType.
        des = _install(monkeypatch, ["A", "B"])
        des.rootComponent.joints._j[1].jointMotion = type("SliderJointMotion", (),
                                                          {"jointType": "B_JOINTTYPE"})()
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["m1"] == REVOLUTE_DOF and md["m2"] == SLIDER_DOF
        assert out["ratio_applied"] is True

    def test_rigid_joint_refused_before_any_link(self, monkeypatch):
        # a rigid joint has no DOF to link; the tool must refuse BEFORE creating a link (nothing to
        # roll back), naming the offending joint.
        des = _install(monkeypatch, ["A", "B"])
        des.rootComponent.joints._j[1].jointMotion = type("RigidJointMotion", (),
                                                          {"jointType": "B_JOINTTYPE"})()
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "'B'" in res["message"] and "rigid" in res["message"]
        assert des.rootComponent.motionLinks.added is None   # no link created to roll back

    def test_default_ratio_is_one(self, monkeypatch):
        des = _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B"))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v1"] == ("real", 1.0) and md["v2"] == ("real", 1.0)
        assert out["ratio"] == 1.0

    def test_negative_ratio_links_reversed_with_magnitude(self, monkeypatch):
        des = _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=-3.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v2"] == ("real", 3.0)      # magnitude only
        assert md["reversed"] is True
        assert out["reversed"] is True

    def test_zero_ratio_rejected(self, monkeypatch):
        _install(monkeypatch, ["A", "B"])
        res = jml.handler(joint_one="A", joint_two="B", ratio=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_non_numeric_ratio_rejected(self, monkeypatch):
        # a ratio that won't float() must error cleanly (not crash), before any link is created.
        des = _install(monkeypatch, ["A", "B"])
        res = jml.handler(joint_one="A", joint_two="B", ratio="banana")
        assert res["isError"] is True and "must be a number" in res["message"]
        # and no link was ever added
        assert des.rootComponent.motionLinks.added is None

    def test_numeric_string_ratio_accepted(self, monkeypatch):
        # "2" is a valid number string -> float() succeeds, magnitude reaches setMotionData.
        des = _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio="2"))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v2"] == ("real", 2.0)
        assert out["ratio"] == 2.0

    def test_ratio_failure_rolls_back_link_and_errors(self, monkeypatch):
        # If setMotionData fails (e.g. BAD_JOINT_DOF), the just-added link is a compute-failed
        # feature — the tool must DELETE it and return an error, NOT leave a broken 1:1 link or
        # claim success.
        des = _install(monkeypatch, ["A", "B"])
        des.rootComponent.motionLinks.add = (
            lambda inp: _link_that_raises(des.rootComponent.motionLinks))
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "could not apply the ratio" in res["message"]
        assert des.rootComponent.motionLinks.last_link.deleted is True   # rolled back

    def test_setmotiondata_failure_reports_platform_refusal(self, monkeypatch):
        # With the correct DOF passed, a remaining setMotionData failure is a genuine platform
        # refusal for this motion pair - the error says so honestly (no wrong-enum guess).
        des = _install(monkeypatch, ["A", "B"])
        des.rootComponent.motionLinks.add = (
            lambda inp: _link_that_raises(des.rootComponent.motionLinks,
                                          "Compute Failed // BAD_JOINT_DOF - wrong type"))
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "platform will not couple" in res["message"]
        assert des.rootComponent.motionLinks.last_link.deleted is True   # rolled back

    def test_setmotiondata_false_return_is_failure(self, monkeypatch):
        # setMotionData returning False (not raising) is still a failure - the tool must not claim a
        # ratio it did not set; it rolls back and errors.
        des = _install(monkeypatch, ["A", "B"])
        link = FakeMotionLink()
        link.setMotionData = lambda *a, **k: False
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "could not apply the ratio" in res["message"]
        assert link.deleted is True


def _slider(des, index, direction):
    """Give joint `index` a SLIDER motion whose slideDirectionVector points along `direction`
    (a None direction is the unreadable vector the API also returns off a JointInput's motion)."""
    j = des.rootComponent.joints._j[index]
    vec = None if direction is None else FakeVector3D(*direction)
    j.jointMotion = type("SliderJointMotion", (),
                         {"jointType": f"{j.name}_JOINTTYPE", "slideDirectionVector": vec})()
    return j


class TestMirrorOrTranslateTeaching:
    """A linked slider pair's actual travel (mirror vs together) is not computable from the tool's
    readable state - measured live: a reversed link over opposed slideDirectionVectors MIRRORED,
    because each joint's occurrence ordering sets which part its slide value moves. So the payload
    teaches the drive-and-read check and never asserts a verdict."""

    def _link(self, monkeypatch, d1, d2, ratio):
        des = _install(monkeypatch, ["SlideL", "SlideR"])
        _slider(des, 0, d1)
        _slider(des, 1, d2)
        return _payload(jml.handler(joint_one="SlideL", joint_two="SlideR", ratio=ratio))

    def test_a_slider_pair_gets_the_teaching_note_not_a_verdict(self, monkeypatch):
        out = self._link(monkeypatch, (1, 0, 0), (-1, 0, 0), -1)
        assert "MIRROR OR TRANSLATE" in out["note"]
        assert "joint_drive" in out["note"] and "drive back to 0" in out["note"]
        assert "world_motion" not in out and "slide_dot" not in out

    def test_the_note_fires_regardless_of_ratio_sign(self, monkeypatch):
        # the trap is not confined to reversed links - a forward link over opposed directions is
        # just as undetermined from here.
        out = self._link(monkeypatch, (0, 1, 0), (0, 1, 0), 2)
        assert "MIRROR OR TRANSLATE" in out["note"]

    def test_a_null_slide_vector_still_gets_the_note(self, monkeypatch):
        # the note keys on the JOINT KIND, not the vector - an unreadable vector changes nothing
        # about the trap.
        out = self._link(monkeypatch, (1, 0, 0), None, -1)
        assert "MIRROR OR TRANSLATE" in out["note"]

    def test_revolute_pair_gets_no_slider_note(self, monkeypatch):
        des = _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=-1))
        assert "MIRROR OR TRANSLATE" not in out["note"]
        assert des.rootComponent.motionLinks.last_link.motion_data["reversed"] is True

    def test_mixed_slider_and_revolute_gets_no_slider_note(self, monkeypatch):
        des = _install(monkeypatch, ["Slide", "Spin"])
        _slider(des, 0, (1, 0, 0))
        out = _payload(jml.handler(joint_one="Slide", joint_two="Spin", ratio=-1))
        assert "MIRROR OR TRANSLATE" not in out["note"]


def _link_that_raises(mls, msg="joint motion type cannot be linked"):
    link = FakeMotionLink()
    def boom(*a, **k):
        raise RuntimeError(msg)
    link.setMotionData = boom
    mls.last_link = link
    return link


def _bind_link(mls, link):
    def add(inp):
        mls.last_link = link
        return link
    return add
