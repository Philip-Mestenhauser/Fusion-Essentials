"""Unit tests for ``joint_drive`` — the Drive Joints command (set a joint's value).

Pinned (no live Fusion): the type gate (only revolute/slider/cylindrical drivable; rigid/ball refused),
the angle-vs-distance argument matching (a slider rejects angle_deg; a revolute rejects distance), the
degrees->radians and mm->cm conversions onto the API's value setters, the enabled-limit warning, the
value read-back, and the motion-link second-member refusal (driving a joint whose linked partner was
already driven this session is refused before any mutation). The motion classes are NAMED to match the
real adsk classes so the shared _current_joint_type maps them correctly.
"""

import json
import types
import math

import pytest

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


def _occ(referenced=False, parent=None):
    """A joint occurrence stub: isReferencedComponent (+ an optional assemblyContext parent chain) is
    what the xref-scoped refusal reads to decide plain-vs-xref."""
    return types.SimpleNamespace(isReferencedComponent=referenced, assemblyContext=parent)


def _plain_occ():
    return _occ(referenced=False, parent=None)


class FakeJoint:
    def __init__(self, name, motion, occ_one=None, occ_two=None, token=None):
        self.name = name
        self.jointMotion = motion
        self.motionLinks = []      # Joint.motionLinks is a plain sequence (MotionLinkVector)
        # occurrences drive the xref-vs-plain decision; a fake without them reads as NOT provably plain
        # (so the guard stays on - the historical default the refusal tests rely on).
        self.occurrenceOne = occ_one
        self.occurrenceTwo = occ_two
        if token is not None:
            self.entityToken = token


class _Joints:
    def __init__(self, joints):
        self._j = list(joints)
    def itemByName(self, name):
        return next((j for j in self._j if j.name == name), None)


def link_pair(j1, j2):
    """Motion-link two FakeJoints the way the platform reports it: the SAME MotionLink object
    appears in BOTH joints' own motionLinks sequence (a MotionLinkVector reads as a plain list)."""
    class FakeMotionLink:
        jointOne, jointTwo = j1, j2
    ml = FakeMotionLink()
    j1.motionLinks = [ml]
    j2.motionLinks = [ml]


class _Root:
    def __init__(self, joints, asbuilt=()):
        self.joints = _Joints(joints)
        self.asBuiltJoints = _Joints(asbuilt)


class _Design:
    def __init__(self, joints, asbuilt=()):
        self.rootComponent = _Root(joints, asbuilt)
        self.allComponents = []


def _install(monkeypatch, joint, asbuilt=()):
    design = _Design([joint], asbuilt)
    monkeypatch.setattr(jd._common, "design", lambda: design)
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards / type gate ───────────────────────────────────────────────────────

class TestGuards:
    def test_no_value_given_errors(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="J")
        assert res["isError"] is True and "drive" in res["message"].lower()

    def test_unknown_units(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", SliderJointMotion()))
        res = jd.handler(joint_name="J", distance=5, units="furlong")
        assert res["isError"] is True and "unit" in res["message"].lower()

    def test_joint_not_found(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="Ghost", angle_deg=10)
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_as_built_joint_is_drivable_by_name(self, monkeypatch):
        # an as-built REVOLUTE (in root.asBuiltJoints, a separate collection) must be found + driven,
        # not reported "no joint named ...". This is the cold-build case (script-created as-built spin).
        spin = FakeJoint("AsBuiltSpin", RevoluteJointMotion())
        _install(monkeypatch, FakeJoint("Regular", RigidJointMotion()), asbuilt=[spin])
        out = _payload(jd.handler(joint_name="AsBuiltSpin", angle_deg=90))
        assert out["driven"] is True
        assert abs(spin.jointMotion.rotationValue - math.pi / 2) < 1e-9

    def test_rigid_is_refused(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", RigidJointMotion()))
        res = jd.handler(joint_name="J", angle_deg=10)
        assert res["isError"] is True and "revolute" in res["message"].lower()

    def test_slider_rejects_angle(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", SliderJointMotion()))
        res = jd.handler(joint_name="J", angle_deg=10)
        assert res["isError"] is True and "distance" in res["message"].lower()

    def test_revolute_rejects_distance(self, monkeypatch):
        _install(monkeypatch, FakeJoint("J", RevoluteJointMotion()))
        res = jd.handler(joint_name="J", distance=10)
        assert res["isError"] is True and "angle_deg" in res["message"]


# ── revolute drive: degrees -> radians ──────────────────────────────────────

class TestRevoluteDrive:
    def test_angle_set_in_radians(self, monkeypatch):
        j = FakeJoint("Pivot", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=90))
        assert abs(j.jointMotion.rotationValue - math.pi / 2) < 1e-9
        assert out["driven"] is True and out["joint_type"] == "revolute"
        assert out["applied"]["angle_deg"] == 90.0

    def test_value_read_back_in_degrees(self, monkeypatch):
        j = FakeJoint("Pivot", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=45))
        assert abs(out["value_now"]["angle_deg"] - 45.0) < 1e-4

    def test_over_limit_warns(self, monkeypatch):
        m = RevoluteJointMotion()
        m.rotationLimits = FakeLimits(max_on=True, maxv=math.radians(30))   # max 30 deg
        j = FakeJoint("Pivot", m)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=60))        # exceeds 30
        assert "limit_warnings" in out and "maximum" in out["limit_warnings"][0]


# ── slider drive: mm -> cm ──────────────────────────────────────────────────

class TestSliderDrive:
    def test_distance_set_in_cm(self, monkeypatch):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert abs(j.jointMotion.slideValue - 5.0) < 1e-9       # 50 mm -> 5 cm
        assert out["joint_type"] == "slider"

    def test_distance_read_back_in_mm(self, monkeypatch):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert abs(out["value_now"]["distance_mm"] - 50.0) < 1e-4

    def test_inch_distance(self, monkeypatch):
        j = FakeJoint("Rail", SliderJointMotion())
        _install(monkeypatch, j)
        jd.handler(joint_name="Rail", distance=1, units="in")
        assert abs(j.jointMotion.slideValue - 2.54) < 1e-9      # 1 in -> 2.54 cm

    def test_below_min_warns(self, monkeypatch):
        m = SliderJointMotion()
        m.slideLimits = FakeLimits(min_on=True, minv=0.0)        # min 0 cm
        j = FakeJoint("Rail", m)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=-20, units="mm"))   # -2 cm < 0
        assert "limit_warnings" in out and "minimum" in out["limit_warnings"][0]


# ── cylindrical drive: both angle + distance ────────────────────────────────

class TestCylindricalDrive:
    def test_drives_both_values(self, monkeypatch):
        j = FakeJoint("Cyl", CylindricalJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Cyl", angle_deg=30, distance=10, units="mm"))
        assert abs(j.jointMotion.rotationValue - math.radians(30)) < 1e-9
        assert abs(j.jointMotion.slideValue - 1.0) < 1e-9       # 10 mm -> 1 cm
        assert out["applied"] == {"angle_deg": 30.0, "distance": 10.0}
        assert "angle_deg" in out["value_now"] and "distance_mm" in out["value_now"]

    def test_cylindrical_angle_only(self, monkeypatch):
        j = FakeJoint("Cyl", CylindricalJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Cyl", angle_deg=15))
        assert abs(j.jointMotion.rotationValue - math.radians(15)) < 1e-9
        assert j.jointMotion.slideValue == 0.0                  # untouched


# ── motion-link second-member refusal ───────────────────────────────────────
# Driving BOTH members of a motion-linked pair kills the Fusion process; once one member is driven,
# driving its partner is refused for the session.

class _FakeDataFile:
    def __init__(self, urn):
        self.id = urn


class _FakeDoc:
    def __init__(self, name, urn=None):
        self.name = name
        if urn is not None:
            self.dataFile = _FakeDataFile(urn)
        # no urn -> no dataFile attribute at all: reading it raises, like an unsaved doc


class _FakeApp:
    def __init__(self, doc_name="DocA", urn=None):
        self.activeDocument = _FakeDoc(doc_name, urn)


class TestSecondMemberRefusal:
    @pytest.fixture
    def linked_pair(self, monkeypatch):
        """A JawL/JawR slider pair motion-linked at the root; fresh session registry; DocA active.
        Yields (jaw_l, jaw_r, design)."""
        jaw_l = FakeJoint("Slider_JawL", SliderJointMotion())
        jaw_r = FakeJoint("Slider_JawR", SliderJointMotion())
        link_pair(jaw_l, jaw_r)
        design = _Design([jaw_l, jaw_r])
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "app", _FakeApp("DocA"))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        return jaw_l, jaw_r, design

    def test_partner_drive_refused_after_first_member(self, linked_pair):
        jaw_l, jaw_r, _ = linked_pair
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True
        assert "Slider_JawL" in res["message"]           # names the driven partner
        assert jaw_r.jointMotion.slideValue == 0.0       # refused BEFORE mutating

    def test_refusal_reports_current_value(self, linked_pair):
        # The link (in real Fusion) already moved the refused joint; the refusal reports its current
        # value so the caller needs no extra read.
        jaw_l, jaw_r, _ = linked_pair
        jd.handler(joint_name="Slider_JawL", distance=16)
        jaw_r.jointMotion.slideValue = -1.6              # what the link did (cm)
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "-16.0 mm" in res["message"]

    def test_first_linked_drive_names_partner_and_warns(self, linked_pair):
        # A successful drive of a LINKED joint reports the partner + the cycle warning; an
        # unlinked drive carries neither.
        out = _payload(jd.handler(joint_name="Slider_JawL", distance=16))
        assert out["motion_link_partner"] == "Slider_JawR"
        assert "Slider_JawR" in out["note"] and "offset" in out["note"]

    def test_same_member_redrive_allowed(self, linked_pair):
        # Repeatedly driving the SAME member is the safe, supported pattern.
        _, _, _ = linked_pair
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=0))["driven"] is True
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=-5))["driven"] is True

    def test_unlinked_joints_both_drivable(self, monkeypatch):
        a, b = FakeJoint("A", SliderJointMotion()), FakeJoint("B", SliderJointMotion())
        design = _Design([a, b])                          # no motion link
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "app", _FakeApp("DocA"))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        assert _payload(jd.handler(joint_name="A", distance=5))["driven"] is True
        assert _payload(jd.handler(joint_name="B", distance=5))["driven"] is True

    def test_link_inside_subcomponent_is_found(self, monkeypatch):
        # The xref shape: the pair + link live inside an xref'd sub-assembly, NOT on the root - the
        # partner comes off the JOINT'S OWN motionLinks membership, so a joint resolved through the
        # sub-component walk carries its link with it.
        jaw_l = FakeJoint("Slider_JawL", SliderJointMotion())
        jaw_r = FakeJoint("Slider_JawR", SliderJointMotion())
        link_pair(jaw_l, jaw_r)
        design = _Design([])                              # root: no joints
        sub = _Root([jaw_l, jaw_r])
        design.allComponents = [sub]
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "app", _FakeApp("DocA"))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]

    def test_registry_is_per_document_identity(self, monkeypatch, linked_pair):
        # A same-named pair in ANOTHER document (distinct lineage URN, same display name) is not
        # poisoned by the first document's drive - identity is the URN, not the name.
        monkeypatch.setattr(jd, "app", _FakeApp("Doc", urn="urn:lineage:a"))
        jd.handler(joint_name="Slider_JawL", distance=16)
        monkeypatch.setattr(jd, "app", _FakeApp("Doc", urn="urn:lineage:b"))
        assert _payload(jd.handler(joint_name="Slider_JawR", distance=-16))["driven"] is True

    def test_rename_does_not_disarm_guard(self, monkeypatch, linked_pair):
        # The first drive registers under the lineage URN; a document RENAME (name changes,
        # dataFile.id stable) must still refuse the second-member drive.
        jaw_l, jaw_r, _ = linked_pair
        monkeypatch.setattr(jd, "app", _FakeApp("Original", urn="urn:lineage:1"))
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        monkeypatch.setattr(jd, "app", _FakeApp("Renamed", urn="urn:lineage:1"))
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]
        assert jaw_r.jointMotion.slideValue == 0.0       # refused BEFORE mutating

    def test_unsaved_doc_falls_back_to_name_key(self, linked_pair):
        # An unsaved document has no dataFile - the name is the registry key and the guard
        # still functions.
        jaw_l, jaw_r, _ = linked_pair                     # fixture's DocA carries no dataFile
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        assert ("DocA", "Slider_JawL") in jd._driven_this_session
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]

    def test_partial_drive_still_arms_the_guard(self, monkeypatch):
        # A cylindrical drive that lands its rotation and then fails on the slide HAS moved the
        # joint (and, via the link, its partner) - the error must say so, and the session registry
        # must arm anyway, or the both-members xref refusal fails open on exactly this sequence.
        class CylindricalJointMotion:                     # name keys the shared type map
            def __init__(self):
                self.rotationValue = 0.0
                self.rotationLimits = FakeLimits()
                self.slideLimits = FakeLimits()
            @property
            def slideValue(self):
                return 0.0
            @slideValue.setter
            def slideValue(self, v):
                raise RuntimeError("slide jammed")
        a = FakeJoint("Cyl_A", CylindricalJointMotion())
        b = FakeJoint("Cyl_B", SliderJointMotion())
        link_pair(a, b)
        design = _Design([a, b])
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "app", _FakeApp("DocA"))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        res = jd.handler(joint_name="Cyl_A", angle_deg=30, distance=10, units="mm")
        assert res["isError"] is True and "PARTIALLY" in res["message"]
        assert abs(a.jointMotion.rotationValue - math.radians(30)) < 1e-9   # rotation DID land
        res2 = jd.handler(joint_name="Cyl_B", distance=-16)
        assert res2["isError"] is True and "Cyl_A" in res2["message"]       # guard armed


# ── xref-scoping + token keying ─────────────────────────────────────────────
# The second-member refusal is scoped to xref-context pairs (crashes are observed only in xref contexts); a plain
# in-document pair is allowed with a warning. The registry keys on entity token, so rebuilding the
# driven partner clears the block.

class TestXrefScopingAndTokens:
    def _install(self, monkeypatch, jaws, urn=None):
        design = _Design(jaws)
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "app", _FakeApp("DocA", urn=urn))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        return design

    def test_plain_pair_second_member_allowed_with_warning(self, monkeypatch):
        # Both joints wholly native -> driving the second member is ALLOWED (a plain in-document
        # pair survives a both-members drive), with a warning; the drive actually takes.
        jaw_l = FakeJoint("L", SliderJointMotion(), occ_one=_plain_occ(), occ_two=_plain_occ())
        jaw_r = FakeJoint("R", SliderJointMotion(), occ_one=_plain_occ(), occ_two=_plain_occ())
        link_pair(jaw_l, jaw_r)
        self._install(monkeypatch, [jaw_l, jaw_r])
        assert _payload(jd.handler(joint_name="L", distance=16))["driven"] is True
        out = _payload(jd.handler(joint_name="R", distance=-16))
        assert out["driven"] is True                       # NOT refused
        assert abs(jaw_r.jointMotion.slideValue - (-1.6)) < 1e-9   # the drive took
        assert "plain" in out["note"].lower()              # the both-members warning

    def test_xref_pair_second_member_refused(self, monkeypatch):
        # A referenced (xref) occurrence anywhere in the pair -> the second-member drive is refused.
        xr = _occ(referenced=True)
        jaw_l = FakeJoint("L", SliderJointMotion(), occ_one=xr, occ_two=_plain_occ())
        jaw_r = FakeJoint("R", SliderJointMotion(), occ_one=xr, occ_two=_plain_occ())
        link_pair(jaw_l, jaw_r)
        self._install(monkeypatch, [jaw_l, jaw_r])
        assert _payload(jd.handler(joint_name="L", distance=16))["driven"] is True
        res = jd.handler(joint_name="R", distance=-16)
        assert res["isError"] is True and "R" in res["message"]
        assert jaw_r.jointMotion.slideValue == 0.0         # refused BEFORE mutating

    def test_xref_via_referenced_ancestor_refused(self, monkeypatch):
        # The pair is native but nested INSIDE a referenced parent -> refused.
        parent = _occ(referenced=True)
        jaw_l = FakeJoint("L", SliderJointMotion(),
                          occ_one=_occ(parent=parent), occ_two=_occ(parent=parent))
        jaw_r = FakeJoint("R", SliderJointMotion(),
                          occ_one=_occ(parent=parent), occ_two=_occ(parent=parent))
        link_pair(jaw_l, jaw_r)
        self._install(monkeypatch, [jaw_l, jaw_r])
        assert _payload(jd.handler(joint_name="L", distance=16))["driven"] is True
        assert jd.handler(joint_name="R", distance=-16)["isError"] is True

    def test_recreated_partner_new_token_clears_block(self, monkeypatch):
        # xref pair: driving L registers L's token t1. Rebuilding L (new token t2, still linked to R)
        # means driving R does not match the registered token -> allowed. Delete+recreate clears.
        l_old = FakeJoint("L", SliderJointMotion(),
                          occ_one=_occ(referenced=True), occ_two=_occ(referenced=True),
                          token="t1")
        r = FakeJoint("R", SliderJointMotion(),
                      occ_one=_occ(referenced=True), occ_two=_occ(referenced=True), token="tr")
        link_pair(l_old, r)
        design = self._install(monkeypatch, [l_old, r])
        assert _payload(jd.handler(joint_name="L", distance=16))["driven"] is True
        # rebuild L with a new token; R now links to the rebuilt L
        l_new = FakeJoint("L", SliderJointMotion(),
                          occ_one=_occ(referenced=True), occ_two=_occ(referenced=True),
                          token="t2")
        link_pair(l_new, r)
        design.rootComponent.joints = _Joints([l_new, r])
        assert _payload(jd.handler(joint_name="R", distance=-16))["driven"] is True

    def test_same_token_partner_still_refused(self, monkeypatch):
        # The token is stable across a rename: a still-registered partner token keeps the refusal
        # (xref pair) - the token change is what clears it, not merely re-reading.
        l = FakeJoint("L", SliderJointMotion(),
                      occ_one=_occ(referenced=True), occ_two=_occ(referenced=True), token="t1")
        r = FakeJoint("R", SliderJointMotion(),
                      occ_one=_occ(referenced=True), occ_two=_occ(referenced=True), token="tr")
        link_pair(l, r)
        self._install(monkeypatch, [l, r])
        assert _payload(jd.handler(joint_name="L", distance=16))["driven"] is True
        assert jd.handler(joint_name="R", distance=-16)["isError"] is True


# ── the value_now vs applied gate ────────────────────────────────────────────

def _frozen_revolute():
    """A revolute motion whose rotationValue setter lands nowhere - the read-back stays at the
    pre-drive value, the way a chain frozen by a parent-locked member behaves (measured: an
    auto-grounded first component made every drive a silent no-op)."""
    class RevoluteJointMotion:                      # the NAME is what current_joint_type keys on
        def __init__(self):
            self.rotationLimits = FakeLimits()
        @property
        def rotationValue(self):
            return 0.0
        @rotationValue.setter
        def rotationValue(self, v):
            pass
    return RevoluteJointMotion()


class TestDriveTookGate:
    def test_silently_ignored_drive_warns_and_names_the_locked_member(self, monkeypatch):
        j = FakeJoint("J", _frozen_revolute())
        design = _install(monkeypatch, j)
        rotor = types.SimpleNamespace(name="Rotor:1", isGroundToParent=True)
        design.rootComponent.occurrences = types.SimpleNamespace(count=1, item=lambda i: rotor)
        out = _payload(jd.handler(joint_name="J", angle_deg=25))
        assert out["drive_took"] is False
        assert "DID NOT TAKE" in out["note"] and "Rotor:1" in out["note"]

    def test_gate_falls_back_to_pointer_when_no_member_is_locked(self, monkeypatch):
        j = FakeJoint("J", _frozen_revolute())
        design = _install(monkeypatch, j)
        free = types.SimpleNamespace(name="Rotor:1", isGroundToParent=False)
        design.rootComponent.occurrences = types.SimpleNamespace(count=1, item=lambda i: free)
        out = _payload(jd.handler(joint_name="J", angle_deg=25))
        assert out["drive_took"] is False
        assert "ground_to_parent" in out["note"]

    def test_limit_warning_suppresses_the_gate(self, monkeypatch):
        # A clamped drive is already explained by limit_warnings; the gate stays quiet there.
        j = FakeJoint("J", _frozen_revolute())
        j.jointMotion.rotationLimits = FakeLimits(max_on=True, maxv=math.radians(10))
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="J", angle_deg=45))
        assert "limit_warnings" in out and "drive_took" not in out

    def test_a_drive_that_took_is_not_flagged(self, monkeypatch):
        j = FakeJoint("J", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="J", angle_deg=25))
        assert "drive_took" not in out and abs(out["value_now"]["angle_deg"] - 25.0) < 1e-4
