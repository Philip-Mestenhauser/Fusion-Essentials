"""Unit tests for joint_motion_link — couple two joints with a ratio (the Motion Link command).

The live motionLinks.add needs Fusion; the testable logic is the joint name resolution and the
input guards (both names required, distinct, must resolve) plus the ratio plumbing.
"""

import json
import types

import pytest

import adsk.fusion

from conftest import FakeVector3D, _NamedCollection, load_tool

jml = load_tool("joint_motion_link")
jt = load_tool("_joints")          # the ratio codec, to pin the payload against its own output

# The DOF values setMotionData actually wants, straight from the measured enum (never hand-typed).
JMT = adsk.fusion.JointMotionTypes
REVOLUTE_DOF = JMT.RevoluteJointRotateMotionType
SLIDER_DOF = JMT.SliderJointSlideMotionType

# The rack-and-pinion ratio the unit conversion is measured on: this many degrees of pinion per
# millimetre of rack is 0.5 rad per cm, the pair Fusion actually couples on.
RIG_RATIO_DEG_PER_MM = 2.8647889757


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
        # The link's own ModelParameters, in Fusion's native rad/cm - absent until setMotionData
        # parks the pair on them, which is what the tool reads back to publish value_one/value_two.
        self.valueOne = None
        self.valueTwo = None
    def setMotionData(self, m1, v1, m2, v2, reversed_):
        self.motion_data = {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "reversed": reversed_}
        # ValueInput.createByReal is patched to echo ('real', number), so the parameters hold the
        # number the platform was handed.
        self.valueOne = types.SimpleNamespace(value=v1[1])
        self.valueTwo = types.SimpleNamespace(value=v2[1])
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


def FakeComponents(comps):
    """design.allComponents: conftest's shared collection, counted AND iterable alike (measure_api
    allcomponents-design-only) - the two halves a bare list models neither of. It holds the
    components THE ROOT INCLUDED, the contract _common.all_components holds; a collection without
    the root hides every root joint from a design-wide walk."""
    return _NamedCollection(comps)


class FakeDesign:
    def __init__(self, names, asbuilt=(), subs=()):
        self.rootComponent = FakeRoot(names, asbuilt)
        self.allComponents = FakeComponents([self.rootComponent] + list(subs))


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
        j, err = jml.find_joint(des, "Wheel_Spin")
        assert err is None and j.name == "Wheel_Spin"

    def test_finds_as_built_joint(self, monkeypatch):
        # asBuiltJoints is a SEPARATE collection from joints - a joint living only there must still
        # resolve (joint_motion_link must not fork its own root-only lookup).
        des = _install(monkeypatch, ["Wheel_Spin"], asbuilt=["Spin_Link"])
        j, err = jml.find_joint(des, "Spin_Link")
        assert err is None and j is not None and j.name == "Spin_Link"

    def test_unknown_name_returns_none_without_an_error(self, monkeypatch):
        # A miss is (None, None): each caller words its own not-found message off its own listing.
        des = _install(monkeypatch, ["Wheel_Spin"])
        assert jml.find_joint(des, "Ghost") == (None, None)

    def test_a_name_two_components_share_refuses_the_link(self, monkeypatch):
        # The handler's side of the refusal: two components each hold a 'Revolute1', so neither
        # member of the link can be identified and nothing is created.
        def tokened(name, token, comp):
            j = FakeJoint(name)
            j.entityToken = token
            j.parentComponent = type("C", (), {"name": comp})()
            return j
        des = _install(monkeypatch, ["Wheel_Spin"])
        des.rootComponent.joints = FakeJoints([tokened("Revolute1", "t1", "Arm"),
                                               FakeJoint("Wheel_Spin")])
        sub = type("Sub", (), {"joints": FakeJoints([tokened("Revolute1", "t2", "Gripper")]),
                               "asBuiltJoints": FakeJoints([])})()
        des.allComponents = FakeComponents([des.rootComponent, sub])
        res = jml.handler(joint_one="Revolute1", joint_two="Wheel_Spin")
        assert res["isError"] is True
        assert "Arm" in res["message"] and "Gripper" in res["message"]
        assert des.rootComponent.motionLinks.added is None      # nothing was created


class TestAllJoints:
    def _sub(self, joints=(), asbuilt=()):
        return type("Sub", (), {"joints": FakeJoints([FakeJoint(n) for n in joints]),
                                "asBuiltJoints": FakeJoints([FakeJoint(n) for n in asbuilt])})()

    def _wrapper(self, joints):
        return type("Comp", (), {"joints": FakeJoints(joints),
                                 "asBuiltJoints": FakeJoints([])})()

    def test_walks_root_and_subcomponents_and_asbuilt(self):
        # a root-only walk would miss Sub_J / Sub_AB - the under-reporting failure mode this guards.
        root = FakeRoot(["Root_A"], asbuilt=["Root_AB"])
        sub = self._sub(joints=["Sub_J"], asbuilt=["Sub_AB"])
        des = type("D", (), {"rootComponent": root,
                             "allComponents": FakeComponents([root, sub])})()
        names = sorted(j.name for j in jml.all_joints(des))
        assert names == ["Root_A", "Root_AB", "Sub_AB", "Sub_J"]

    def test_the_walk_asks_the_component_collection_and_never_a_prepended_root(self):
        # allComponents already CARRIES the root, so prepending design.rootComponent reads every
        # root joint twice, as two distinct wrappers - and the de-dup cannot always collapse that
        # pair: a joint answering neither a token nor a name keys on id(), which two wrappers never
        # share. Asking only the collection is what keeps the row single.
        def anonymous():
            return type("J", (), {"name": "", "entityToken": None})()
        des = type("D", (), {"rootComponent": self._wrapper([anonymous()]),
                             "allComponents": FakeComponents([self._wrapper([anonymous()])])})()
        assert len(jml.all_joints(des)) == 1

    def test_dedups_one_joint_reached_through_two_component_wrappers(self):
        # the token de-dup, the SECOND line over the joint objects: two readings answering ONE token
        # are one joint. Component wrappers are never identity-stable, so nothing above this can
        # collapse them - without it joint_count double-counts and find_joint refuses its own hit.
        def tokened(name, token):
            return type("J", (), {"name": name, "entityToken": token})()
        wrappers = [self._wrapper([tokened("Root_A", "TOKEN_ROOT_A")]) for _ in range(2)]
        des = type("D", (), {"rootComponent": wrappers[0],
                             "allComponents": FakeComponents(wrappers)})()
        assert [j.name for j in jml.all_joints(des)] == ["Root_A"]

    def test_empty_design_is_safe(self):
        root = FakeRoot([])
        des = type("D", (), {"rootComponent": root, "allComponents": FakeComponents([root])})()
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

    def test_a_successful_rollback_does_not_claim_the_link_remains(self, monkeypatch):
        # deleteMe() answered True - the broken link is gone, so the error must NOT tell the caller
        # to go delete something that no longer exists.
        des = _install(monkeypatch, ["A", "B"])
        des.rootComponent.motionLinks.add = (
            lambda inp: _link_that_raises(des.rootComponent.motionLinks))
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "REMAINS" not in res["message"]
        assert "assembly_edit_relations" not in res["message"]

    def test_a_declined_rollback_says_the_link_REMAINS(self, monkeypatch):
        # deleteMe() returning False leaves exactly the broken DEFAULT-ratio link the rollback exists
        # to prevent. Discarding that bool reports the link as cleaned up when it is still coupling
        # the two joints, so the error names it, its default ratio, and the delete path.
        des = _install(monkeypatch, ["A", "B"])
        link = _link_that_raises(des.rootComponent.motionLinks)
        link.deleteMe = lambda: False
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True
        assert "could not apply the ratio" in res["message"]
        assert "REMAINS" in res["message"]
        assert "1:1" in res["message"]                     # the ratio it is stuck at
        assert "MotionLink1" in res["message"]             # named, so the delete resolves
        assert "assembly_edit_relations(kind='motion_link'" in res["message"]
        assert "action='delete'" in res["message"]

    def test_a_rollback_that_RAISES_also_says_the_link_REMAINS(self, monkeypatch):
        # a raising deleteMe is no more evidence of removal than a False one
        des = _install(monkeypatch, ["A", "B"])
        link = _link_that_raises(des.rootComponent.motionLinks)
        def boom():
            raise RuntimeError("delete refused")
        link.deleteMe = boom
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True and "REMAINS" in res["message"]

    def test_an_unnamed_leftover_link_still_reports_it_remains(self, monkeypatch):
        # the name is unreadable: the message must not quote a placeholder as the delete argument,
        # but the fact that a link was left behind still has to be said
        des = _install(monkeypatch, ["A", "B"])
        link = _NamelessMotionLink()
        link.deleteMe = lambda: False
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        res = jml.handler(joint_one="A", joint_two="B", ratio=2.0)
        assert res["isError"] is True and "REMAINS" in res["message"]
        assert "name could not be read" in res["message"]
        assert "''" not in res["message"]

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


class TestRatioUnits:
    """The ratio crosses the wire in DISPLAY units per DOF (deg for a rotation, mm for a slide) and
    reaches setMotionData in Fusion's native rad/cm. A same-kind pair's factors cancel; a mixed pair
    converts by both. The codec is shared with the re-value path (assembly_edit_relations
    set_values), and both publish its three facts."""

    def test_a_revolute_pair_still_sends_the_bare_ratio(self, monkeypatch):
        # BACK-COMPAT: deg-to-deg cancels, so a rev/rev caller sends the number itself, not a
        # scaled one.
        des = _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert des.rootComponent.motionLinks.last_link.motion_data["v2"] == ("real", 2.0)
        assert out["value_one"] == 1.0 and out["value_two"] == 2.0
        assert out["ratio_units"] == "deg of joint_two per deg of joint_one"
        assert out["value_units"] == "value_one in rad, value_two in rad"

    def test_a_slider_pair_still_sends_the_bare_ratio(self, monkeypatch):
        # the other same-kind pair: mm-to-mm cancels the same way.
        des = _install(monkeypatch, ["SlideL", "SlideR"])
        _slider(des, 0, (1, 0, 0))
        _slider(des, 1, (1, 0, 0))
        out = _payload(jml.handler(joint_one="SlideL", joint_two="SlideR", ratio=-3.0))
        assert des.rootComponent.motionLinks.last_link.motion_data["v2"] == ("real", 3.0)
        assert out["value_two"] == 3.0
        assert out["ratio_units"] == "mm of joint_two per mm of joint_one"

    def test_the_rack_and_pinion_ratio_reaches_the_api_in_native_units(self, monkeypatch):
        # THE MEASURED PAIR: 2.8647889757 deg of pinion per mm of rack IS 0.5 rad per cm. Sending
        # the display number raw couples 5.7x too fast.
        des = _install(monkeypatch, ["Rack", "Pinion"])
        _slider(des, 0, (1, 0, 0))
        out = _payload(jml.handler(joint_one="Rack", joint_two="Pinion", ratio=-RIG_RATIO_DEG_PER_MM))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v1"] == ("real", 1.0)
        assert md["v2"][0] == "real" and md["v2"][1] == pytest.approx(0.5, abs=1e-9)
        assert md["v2"][1] != pytest.approx(RIG_RATIO_DEG_PER_MM, abs=1e-6)
        assert md["reversed"] is True                       # the sign is still the reversal flag
        assert out["ratio"] == -RIG_RATIO_DEG_PER_MM        # the caller's own number, unscaled
        assert out["value_two"] == pytest.approx(0.5, abs=1e-9)
        assert out["ratio_units"] == "deg of joint_two per mm of joint_one"
        assert out["value_units"] == "value_one in cm, value_two in rad"
        assert "2.8647889757 deg of joint_two per 1 mm of joint_one" in out["interpreted"]

    def test_a_revolute_to_slider_ratio_converts_the_other_way(self, monkeypatch):
        des = _install(monkeypatch, ["Spin", "Slide"])
        _slider(des, 1, (1, 0, 0))
        out = _payload(jml.handler(joint_one="Spin", joint_two="Slide", ratio=2.0))
        md = des.rootComponent.motionLinks.last_link.motion_data
        assert md["v2"][1] == pytest.approx(11.4591559026, abs=1e-9)
        assert out["ratio_units"] == "mm of joint_two per deg of joint_one"

    def test_the_published_facts_are_the_codecs_own(self, monkeypatch):
        # PARITY with the re-value path: both writers publish exactly what the one codec returned for
        # the same DOF pair and ratio, so a divergence between them reds here.
        des = _install(monkeypatch, ["Rack", "Pinion"])
        _slider(des, 0, (1, 0, 0))
        out = _payload(jml.handler(joint_one="Rack", joint_two="Pinion", ratio=RIG_RATIO_DEG_PER_MM))
        facts = jt.link_ratio_values(SLIDER_DOF, REVOLUTE_DOF, RIG_RATIO_DEG_PER_MM)[2]
        assert {k: out[k] for k in facts} == facts

    def test_a_dof_that_answers_no_unit_is_sent_unconverted_and_says_so(self, monkeypatch):
        # a DOF outside the rotate/slide tables establishes no display unit, so scaling it would be
        # a guess: the magnitude goes out as given and the payload withholds both unit names.
        des = _install(monkeypatch, ["A", "B"])
        monkeypatch.setattr(jml, "motion_link_dof", lambda j: (object(), None))
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=RIG_RATIO_DEG_PER_MM))
        assert des.rootComponent.motionLinks.last_link.motion_data["v2"] == (
            "real", RIG_RATIO_DEG_PER_MM)
        assert out["ratio_units"] is None and out["value_units"] is None
        assert "NO unit conversion" in out["interpreted"]

    def test_the_note_does_not_promise_the_partner_moves(self, monkeypatch):
        # joint_drive's receipt is what answers whether the link couples; this create reads nothing
        # about the coupling, so it points at that read instead of asserting proportional motion.
        _install(monkeypatch, ["A", "B"])
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert "not claimed here" in out["note"] and "joint_drive" in out["note"]
        assert "moves the other proportionally" not in out["note"]
        assert "REFUSES the second member" in out["note"]      # the measured warning stays


class TestValueReadBack:
    """value_one/value_two are the link's OWN parameters read back off the MotionLink after
    setMotionData - the same pair assembly_get's relations row and the re-value path publish under
    those names. The numbers HANDED to the API are stated in 'interpreted' instead, so no key on
    this payload lets a sent number be read as a measured one."""

    def _parks(self, monkeypatch, one, two):
        """A design whose link parks (one, two) on its parameters whatever it is sent - None for a
        parameter that does not read at all."""
        des = _install(monkeypatch, ["A", "B"])
        link = FakeMotionLink()

        def park(m1, v1, m2, v2, reversed_):
            link.motion_data = {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "reversed": reversed_}
            link.valueOne = None if one is None else types.SimpleNamespace(value=one)
            link.valueTwo = None if two is None else types.SimpleNamespace(value=two)
            return True
        link.setMotionData = park
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        return link

    def test_the_published_pair_is_the_one_the_link_holds_not_the_one_sent(self, monkeypatch):
        # the discriminating pair: the platform stores 2:4 for a sent 1:2 - the same coupling in
        # different numbers - so a payload echoing what createByReal was given reads 1.0/2.0 here.
        link = self._parks(monkeypatch, 2.0, 4.0)
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert link.motion_data["v1"] == ("real", 1.0) and link.motion_data["v2"] == ("real", 2.0)
        assert out["value_one"] == 2.0 and out["value_two"] == 4.0
        assert "READ BACK" in out["note"]

    def test_the_mixed_pair_publishes_the_native_number_the_link_holds(self, monkeypatch):
        # the rack-and-pinion create: the link holds 0.5 rad per cm, never the caller's display
        # number, and 'ratio' beside it is still the caller's own.
        des = _install(monkeypatch, ["Rack", "Pinion"])
        _slider(des, 0, (1, 0, 0))
        out = _payload(jml.handler(joint_one="Rack", joint_two="Pinion",
                                   ratio=RIG_RATIO_DEG_PER_MM))
        assert out["value_one"] == 1.0
        assert out["value_two"] == pytest.approx(0.5, abs=1e-9)
        assert out["value_two"] != pytest.approx(RIG_RATIO_DEG_PER_MM, abs=1e-6)
        assert out["ratio"] == RIG_RATIO_DEG_PER_MM

    def test_a_link_left_holding_a_different_coupling_is_an_error(self, monkeypatch):
        # setMotionData answers True while the parameters read 1:1 - the wrong-ratio failure no
        # other field in this result reveals. The link COMPUTED, so it is reported, not deleted.
        link = self._parks(monkeypatch, 1.0, 1.0)
        res = jml.handler(joint_one="A", joint_two="B", ratio=4.0)
        assert res["isError"] is True
        assert "did not take" in res["message"] and "1.0:1.0" in res["message"]
        assert "MotionLink1" in res["message"] and "REMAINS" in res["message"]
        assert "action='set_values'" in res["message"] and "action='delete'" in res["message"]
        assert link.deleted is False

    def test_a_read_back_off_the_converted_value_by_float_noise_still_passes(self, monkeypatch):
        # the converted number carries a float tail (0.5000000000080081 for the rig ratio); a
        # platform storing the clean 0.5 differs only in that tail and has taken the ratio.
        des = _install(monkeypatch, ["Rack", "Pinion"])
        _slider(des, 0, (1, 0, 0))
        link = FakeMotionLink()

        def rounded(m1, v1, m2, v2, reversed_):
            link.motion_data = {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "reversed": reversed_}
            link.valueOne = types.SimpleNamespace(value=1.0)
            link.valueTwo = types.SimpleNamespace(value=0.5)
            return True
        link.setMotionData = rounded
        des.rootComponent.motionLinks.add = _bind_link(des.rootComponent.motionLinks, link)
        out = _payload(jml.handler(joint_one="Rack", joint_two="Pinion",
                                   ratio=RIG_RATIO_DEG_PER_MM))
        assert out["value_two"] == 0.5

    def test_unreadable_parameters_publish_nulls_and_say_the_pair_is_unconfirmed(self, monkeypatch):
        # a pair that did not read is no evidence the ratio failed - and none that it took, so both
        # numbers are withheld rather than filled in with what was sent.
        self._parks(monkeypatch, None, None)
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert out["value_one"] is None and out["value_two"] is None
        assert "UNCONFIRMED" in out["note"] and "assembly_get" in out["note"]
        assert out["linked"] is True and out["ratio_applied"] is True and out["ratio"] == 2.0

    def test_a_HALF_read_pair_is_unconfirmed_too_when_only_valueTwo_is_unreadable(self, monkeypatch):
        # ONE parameter reading is not a confirmed coupling: the ratio gate compares read_two /
        # read_one, so a pair holding a null cannot be compared at all and answers "no mismatch" -
        # silence that is no evidence the ratio took. The clause must fire on EITHER null, not only
        # on both (an `and` here ships a lone null under the unqualified READ BACK sentence).
        link = self._parks(monkeypatch, 2.0, None)
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert "UNCONFIRMED" in out["note"] and "assembly_get" in out["note"]
        # the shipped shape: the readable parameter is published as READ (2.0, not the sent 1.0) and
        # the unreadable one stays null - the sent 2.0 is never poured into the gap.
        assert out["value_one"] == 2.0
        assert out["value_two"] is None
        assert link.motion_data["v1"] == ("real", 1.0) and link.motion_data["v2"] == ("real", 2.0)
        # _payload already asserted isError is False: an uncomparable pair is NOT the wrong-ratio
        # error, and the link that computed is left alone.
        assert link.deleted is False
        assert out["linked"] is True and out["ratio_applied"] is True and out["ratio"] == 2.0

    def test_the_mirror_HALF_read_pair_is_unconfirmed_when_only_valueOne_is_unreadable(self, monkeypatch):
        # the other half: valueOne is the null. Same verdict, and value_two publishes the number the
        # link holds (4.0) rather than the 2.0 that was sent.
        link = self._parks(monkeypatch, None, 4.0)
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert "UNCONFIRMED" in out["note"] and "assembly_get" in out["note"]
        assert out["value_one"] is None
        assert out["value_two"] == 4.0
        assert link.deleted is False
        assert out["linked"] is True and out["ratio_applied"] is True

    def test_a_fully_read_pair_carries_NO_unconfirmed_clause(self, monkeypatch):
        # the other side of the same boundary: two numbers that read and agree are confirmed, so the
        # clause must NOT fire - a payload that always appends it would report every good link as
        # unconfirmed and its two published numbers as null.
        self._parks(monkeypatch, 2.0, 4.0)
        out = _payload(jml.handler(joint_one="A", joint_two="B", ratio=2.0))
        assert "UNCONFIRMED" not in out["note"]
        assert out["value_one"] == 2.0 and out["value_two"] == 4.0


class _NamelessMotionLink(FakeMotionLink):
    """A MotionLink whose .name RAISES - the leftover link nothing can be addressed by."""
    def __init__(self):
        super().__init__()
        def boom(*a, **k):
            raise RuntimeError("ratio refused")
        self.setMotionData = boom

    @property
    def name(self):
        raise RuntimeError("name unreadable")


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
