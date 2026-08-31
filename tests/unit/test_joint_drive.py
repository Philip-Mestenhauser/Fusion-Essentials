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


@pytest.fixture(autouse=True)
def _isolated_document_keys():
    """_write_guard's per-instance key registry is SESSION state shared with every other consumer
    of document_key, and it outlives one call by design. Cleared around every test in this file so
    the minted tokens are deterministic and no fake document is left for another file to scan."""
    jd._write_guard._UNSAVED_DOC_KEYS.clear()
    yield
    jd._write_guard._UNSAVED_DOC_KEYS.clear()


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
    what the xref-scoped refusal reads to decide plain-vs-xref. It carries NO transform2, so its
    placement is unreadable - the shape the 'no moved key' branch answers."""
    return types.SimpleNamespace(isReferencedComponent=referenced, assemblyContext=parent)


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Matrix:
    """An occurrence transform2: a translation in CENTIMETRES plus the rotation basis, handed back
    the way Matrix3D does (getAsCoordinateSystem returns origin, xAxis, yAxis, zAxis)."""
    def __init__(self, pos, basis):
        self.translation = _Vec(*pos)
        self._basis = basis

    def getAsCoordinateSystem(self):
        return (self.translation,) + tuple(_Vec(*b) for b in self._basis)


class _Placed:
    """A joint member whose placement the fake motion moves, the way a solved mechanism moves the
    part on one side of the joint. pos is in cm (the API's own length unit)."""
    def __init__(self, name, pos=(0.0, 0.0, 0.0)):
        self.name = name
        self.fullPathName = name
        self.isReferencedComponent = False
        self.assemblyContext = None
        self.pos = list(pos)
        self.basis = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    @property
    def transform2(self):
        return _Matrix(self.pos, self.basis)


def _slider_moving(target, axis=(1.0, 0.0, 0.0), vector=(1.0, 0.0, 0.0), turn_vector=None,
                   also=None):
    """A slider whose slide value displaces `target` along `axis` (cm per cm of slide); `also` is an
    optional second (target, axis) pair the same slide moves. When turn_vector is given the direction
    vector CHANGES on the drive, so a receipt reporting the pre-drive vector can be told from one
    that re-read it afterwards."""
    movers = [(target, axis)] + ([also] if also else [])

    class SliderJointMotion:                        # the NAME is what current_joint_type keys on
        def __init__(self):
            self.slideLimits = FakeLimits()
            self.slideDirectionVector = _Vec(*vector)
            self._v = 0.0

        @property
        def slideValue(self):
            return self._v

        @slideValue.setter
        def slideValue(self, v):
            step = v - self._v
            self._v = v
            for part, ax in movers:
                part.pos = [p + step * a for p, a in zip(part.pos, ax)]
            if turn_vector is not None:
                self.slideDirectionVector = _Vec(*turn_vector)
    return SliderJointMotion()


def _revolute_moving(target, vector=(0.0, 0.0, 1.0)):
    """A revolute whose rotation value spins `target`'s basis about world z by that angle."""
    class RevoluteJointMotion:                      # the NAME is what current_joint_type keys on
        def __init__(self):
            self.rotationLimits = FakeLimits()
            self.rotationAxisVector = _Vec(*vector)
            self._v = 0.0

        @property
        def rotationValue(self):
            return self._v

        @rotationValue.setter
        def rotationValue(self, v):
            self._v = v
            c, s = math.cos(v), math.sin(v)
            target.basis = [[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]]
    return RevoluteJointMotion()


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
    """A joints/asBuiltJoints collection: count/item (the shared _joints walk find_joint resolves a
    name over, which must see EVERY hit to refuse a shared name) plus itemByName."""
    def __init__(self, joints):
        self._j = list(joints)
    @property
    def count(self):
        return len(self._j)
    def item(self, i):
        return self._j[i]
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

    def test_an_over_limit_command_is_REFUSED_before_assignment(self, monkeypatch):
        # measured live: Fusion IGNORES a beyond-limit drive (the value stays put; it never
        # clamps) - so the command is refused before anything is assigned.
        m = RevoluteJointMotion()
        m.rotationLimits = FakeLimits(max_on=True, maxv=math.radians(30))   # max 30 deg
        j = FakeJoint("Pivot", m)
        _install(monkeypatch, j)
        res = jd.handler(joint_name="Pivot", angle_deg=60)                  # exceeds 30
        assert res["isError"] is True
        assert "above the enabled maximum" in res["message"] and "30.0 deg" in res["message"]
        assert j.jointMotion.rotationValue == 0.0                           # nothing was assigned

    def test_a_command_exactly_at_the_bound_is_driven(self, monkeypatch):
        # measured live: a command exactly AT an enabled bound lands on it - only STRICTLY
        # beyond is refused.
        m = RevoluteJointMotion()
        m.rotationLimits = FakeLimits(max_on=True, maxv=math.radians(30))
        j = FakeJoint("Pivot", m)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=30))
        assert abs(out["value_now"]["angle_deg"] - 30.0) < 1e-3


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

    def test_a_below_minimum_command_is_REFUSED_before_assignment(self, monkeypatch):
        # measured live for rotation; the slide path shares the refusal (Fusion ignores an
        # out-of-range drive - nothing would move).
        m = SliderJointMotion()
        m.slideLimits = FakeLimits(min_on=True, minv=0.0)        # min 0 cm
        j = FakeJoint("Rail", m)
        _install(monkeypatch, j)
        res = jd.handler(joint_name="Rail", distance=-20, units="mm")      # -2 cm < 0
        assert res["isError"] is True
        assert "below the enabled minimum" in res["message"] and "mm" in res["message"]
        assert j.jointMotion.slideValue == 0.0                   # nothing was assigned


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

    def save_as(self, urn):
        """The document is SAVED without closing: the same open document (the same handle, so the
        key registry still matches it) now answers a data-file id where it answered none."""
        self.dataFile = _FakeDataFile(urn)


class _FakeApp:
    def __init__(self, doc_name="DocA", urn=None):
        self.activeDocument = _FakeDoc(doc_name, urn)


class TestDocumentKeyIdentity:
    """What THIS consumer does with the shared document key (_write_guard.document_key).

    The key itself - the lineage urn for a saved document, a per-instance token matched by document
    HANDLE for an unsaved one (measured live, two open never-saved documents both answer
    'Untitled', so a name is not a stand-in), and the backward prune walk - is pinned once in
    test_write_guard.py. Pinned here is joint_drive's own branch on it: the stand-in it words when
    no document reads at all, and the consequence for the driven-joint registry, whose entityToken
    half is DOCUMENT-LOCAL and repeats across documents - so a key that merged two documents would
    refuse a safe drive in one because a DIFFERENT document's joint was driven.
    """

    def test_no_readable_document_is_not_a_document_key(self, monkeypatch):
        # document_key answers None here - nothing read, so nothing to mint for. This consumer's
        # stand-in is a string that is not a document either, so it matches no real one.
        monkeypatch.setattr(jd._write_guard, "app", types.SimpleNamespace())   # activeDocument raises
        key = jd._doc_key()
        assert key == "<no document>"
        assert not key.startswith("unsaved:")               # and it mints nothing
        assert jd._write_guard._UNSAVED_DOC_KEYS == []

    def test_several_closed_documents_are_all_evicted_in_one_pass(self, monkeypatch):
        # The SHARED eviction, reached through this consumer. It walks the registry BACKWARDS so a
        # deletion cannot slide the next entry past the cursor, and so the index it holds stays
        # inside a list that is shrinking under it. Only a registry holding MORE THAN ONE dead entry
        # tells the two walks apart: a forward walk skips the entry that slid into the freed slot
        # and then indexes past the end, raising IndexError out of _doc_key() and so out of
        # handler(). A --keep-open session full of scratch documents produces exactly this shape.
        app = _FakeApp("Untitled")
        docs = [app.activeDocument, _FakeDoc("Untitled"), _FakeDoc("Untitled")]
        monkeypatch.setattr(jd._write_guard, "app", app)
        keys = []
        for d in docs:
            d.isValid = True
            app.activeDocument = d
            keys.append(jd._doc_key())
        assert len(jd._write_guard._UNSAVED_DOC_KEYS) == 3 and len(set(keys)) == 3
        docs[0].isValid = False
        docs[1].isValid = False                    # two dead entries, adjacent, at the front
        app.activeDocument = docs[2]
        assert jd._doc_key() == keys[2]       # the survivor keeps its own key
        assert [k for _d, k in jd._write_guard._UNSAVED_DOC_KEYS] == [keys[2]]

    def test_a_drive_in_one_unsaved_document_does_not_refuse_the_partner_in_another(self, monkeypatch):
        # The end-to-end consequence, holding the JOINTS constant and varying only the document:
        # driving JawL in one never-saved document must not refuse JawR in a different one.
        jaw_l = FakeJoint("Slider_JawL", SliderJointMotion())
        jaw_r = FakeJoint("Slider_JawR", SliderJointMotion())
        link_pair(jaw_l, jaw_r)
        design = _Design([jaw_l, jaw_r])
        monkeypatch.setattr(jd._common, "design", lambda: design)
        monkeypatch.setattr(jd, "_driven_this_session", set())
        app = _FakeApp("Untitled")
        monkeypatch.setattr(jd._write_guard, "app", app)
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        app.activeDocument = _FakeDoc("Untitled")          # a DIFFERENT unsaved document
        out = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert out["isError"] is False, out
        # and the guard is not disarmed generally: back in the FIRST document it still refuses.
        app.activeDocument = jd._write_guard._UNSAVED_DOC_KEYS[0][0]
        assert jd.handler(joint_name="Slider_JawR", distance=-16)["isError"] is True


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
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("DocA"))
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
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("DocA"))
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
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("DocA"))
        monkeypatch.setattr(jd, "_driven_this_session", set())
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]

    def test_registry_is_per_document_identity(self, monkeypatch, linked_pair):
        # A same-named pair in ANOTHER document (distinct lineage URN, same display name) is not
        # poisoned by the first document's drive - identity is the URN, not the name.
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("Doc", urn="urn:lineage:a"))
        jd.handler(joint_name="Slider_JawL", distance=16)
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("Doc", urn="urn:lineage:b"))
        assert _payload(jd.handler(joint_name="Slider_JawR", distance=-16))["driven"] is True

    def test_rename_does_not_disarm_guard(self, monkeypatch, linked_pair):
        # The first drive registers under the lineage URN; a document RENAME (name changes,
        # dataFile.id stable) must still refuse the second-member drive.
        jaw_l, jaw_r, _ = linked_pair
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("Original", urn="urn:lineage:1"))
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("Renamed", urn="urn:lineage:1"))
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]
        assert jaw_r.jointMotion.slideValue == 0.0       # refused BEFORE mutating

    def test_unsaved_doc_keys_on_a_per_instance_token_and_the_guard_still_functions(self, linked_pair):
        # An unsaved document has no dataFile, so the registry keys on a per-instance token minted
        # for that document - NOT its name. Within the one document the guard is unchanged: the
        # second member is still refused.
        jaw_l, jaw_r, _ = linked_pair                     # fixture's DocA carries no dataFile
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        keys = [k for k, _ in jd._driven_this_session]
        assert keys and all(k.startswith("unsaved:") for k in keys), keys
        assert "DocA" not in keys                         # the name is not the identity
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]

    def test_guard_survives_the_save_that_re_keys_the_document(self, monkeypatch, linked_pair):
        # THE BITE, and it fails toward the crash: a never-saved document holding referenced
        # components is driven, then SAVED. The document key changes from the minted token to the
        # data-file id without the document closing, so the entry parked under the token stops
        # matching and the partner reads as never driven - the both-members refusal, which exists
        # because driving both members of a motion-linked pair in an xref context has killed the
        # Fusion process, does not fire. The rename announcement carries the entry across.
        jaw_l, jaw_r, _ = linked_pair
        app = _FakeApp("Untitled")                          # no dataFile - never saved
        monkeypatch.setattr(jd._write_guard, "app", app)
        assert _payload(jd.handler(joint_name="Slider_JawL", distance=16))["driven"] is True
        app.activeDocument.save_as("urn:lineage:saved")     # doc_save_as, same open document
        res = jd.handler(joint_name="Slider_JawR", distance=-16)
        assert res["isError"] is True and "Slider_JawL" in res["message"]
        assert jaw_r.jointMotion.slideValue == 0.0          # refused BEFORE mutating

    def test_the_carried_entry_keeps_its_own_joint_token(self, monkeypatch, linked_pair):
        # Only the DOCUMENT half of the key moves. The entity-token half is what makes a
        # delete+recreate of the driven joint clear the block and a rename keep it; carrying an
        # entry that lost it would arm the guard for every joint in the document.
        jaw_l, jaw_r, _ = linked_pair
        jaw_l.entityToken = "tok:jawL"
        app = _FakeApp("Untitled")
        monkeypatch.setattr(jd._write_guard, "app", app)
        jd.handler(joint_name="Slider_JawL", distance=16)
        app.activeDocument.save_as("urn:lineage:saved")
        jd._doc_key()                                       # the read that detects the flip
        assert jd._driven_this_session == {("urn:lineage:saved", "tok:jawL")}

    def test_a_save_in_one_document_does_not_re_key_anothers_entries(self, monkeypatch, linked_pair):
        # The announcement is broadcast but names ONE key: an entry registered against a different
        # document must keep its own key, or saving document A silently moves document B's
        # driven-joint entries onto A's new id and refuses a safe drive there.
        jd._driven_this_session.add(("unsaved:other", "tok:elsewhere"))
        app = _FakeApp("Untitled")
        monkeypatch.setattr(jd._write_guard, "app", app)
        jd.handler(joint_name="Slider_JawL", distance=16)
        app.activeDocument.save_as("urn:lineage:saved")
        jd._doc_key()
        assert ("unsaved:other", "tok:elsewhere") in jd._driven_this_session

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
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("DocA"))
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
        monkeypatch.setattr(jd._write_guard, "app", _FakeApp("DocA", urn=urn))
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
    def test_silently_ignored_drive_is_an_ERROR_naming_the_locked_member(self, monkeypatch):
        # a detected no-take is a FAILED drive: isError, never a success wearing a warning.
        j = FakeJoint("J", _frozen_revolute())
        design = _install(monkeypatch, j)
        rotor = types.SimpleNamespace(name="Rotor:1", isGroundToParent=True)
        design.rootComponent.occurrences = types.SimpleNamespace(count=1, item=lambda i: rotor)
        res = jd.handler(joint_name="J", angle_deg=25)
        assert res["isError"] is True
        assert "DID NOT TAKE" in res["message"] and "Rotor:1" in res["message"]

    def test_gate_falls_back_to_pointer_when_no_member_is_locked(self, monkeypatch):
        j = FakeJoint("J", _frozen_revolute())
        design = _install(monkeypatch, j)
        free = types.SimpleNamespace(name="Rotor:1", isGroundToParent=False)
        design.rootComponent.occurrences = types.SimpleNamespace(count=1, item=lambda i: free)
        res = jd.handler(joint_name="J", angle_deg=25)
        assert res["isError"] is True
        assert "ground_to_parent" in res["message"]

    def test_a_within_limits_no_take_is_still_an_ERROR(self, monkeypatch):
        # enabled limits must not excuse the verify gate: a command INSIDE the limits that the
        # chain silently ignores is a no-take like any other.
        j = FakeJoint("J", _frozen_revolute())
        j.jointMotion.rotationLimits = FakeLimits(min_on=True, minv=math.radians(-50),
                                                  max_on=True, maxv=math.radians(50))
        _install(monkeypatch, j)
        res = jd.handler(joint_name="J", angle_deg=45)                     # inside +/-50
        assert res["isError"] is True and "DID NOT TAKE" in res["message"]

    def test_a_cylindrical_partial_drive_names_what_landed(self, monkeypatch):
        # rotation lands, slide is silently ignored: the receipt must say the mechanism MOVED
        # and which value landed - a blanket frozen-chain diagnosis contradicts the observed
        # rotation.
        class CylindricalJointMotion:               # the NAME is what current_joint_type keys on
            def __init__(self):
                self.rotationValue = 0.0
                self.rotationLimits = FakeLimits()
                self.slideLimits = FakeLimits()
            @property
            def slideValue(self):
                return 0.0
            @slideValue.setter
            def slideValue(self, v):
                pass                                # accepted, lands nowhere
        j = FakeJoint("Cyl", CylindricalJointMotion())
        _install(monkeypatch, j)
        res = jd.handler(joint_name="Cyl", angle_deg=30, distance=50, units="mm")
        assert res["isError"] is True
        assert "PARTIAL" in res["message"] and "angle landed at 30.0 deg" in res["message"]
        assert "slide 0.0 mm vs commanded 50.0 mm" in res["message"]
        assert "parent-locked" not in res["message"]

    def test_a_drive_that_took_is_not_flagged(self, monkeypatch):
        j = FakeJoint("J", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="J", angle_deg=25))
        assert "drive_took" not in out and abs(out["value_now"]["angle_deg"] - 25.0) < 1e-4


# ── equivalent-pose semantics: a stored angle can carry full turns the command does not ──
# A joint whose stored value reads 720 deg, commanded to 0 deg, is ALREADY at the commanded physical
# pose. That is an equivalent pose, never a failed drive.

def _frozen_revolute_at(deg):
    """A revolute whose stored value is pinned at `deg` - the setter lands nowhere, so the read-back
    keeps a full-turn count the command did not carry."""
    class RevoluteJointMotion:                      # the NAME is what current_joint_type keys on
        def __init__(self):
            self.rotationLimits = FakeLimits()
        @property
        def rotationValue(self):
            return math.radians(deg)
        @rotationValue.setter
        def rotationValue(self, v):
            pass
    return RevoluteJointMotion()


class TestEquivalentPose:
    def test_zero_command_at_720_is_equivalent_not_failed(self, monkeypatch):
        j = FakeJoint("Crank", _frozen_revolute_at(720))
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=0))
        assert out["equivalent_pose"] is True
        assert "drive_took" not in out                       # NOT a failed drive
        assert "DID NOT TAKE" not in out["note"]             # no grounded-chain blame either
        assert "modulo 360" in out["note"]
        assert out["value_now"]["angle_deg"] == 720.0        # the full-turn value, reported honestly
        assert out["value_now"]["angle_deg_normalized"] == 0.0

    def test_full_turn_command_at_zero_is_equivalent(self, monkeypatch):
        # the wrap-around direction: at 0 deg, commanding 360 deg is the same pose (d == 360).
        j = FakeJoint("Crank", _frozen_revolute_at(0))
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=360))
        assert out["equivalent_pose"] is True and "drive_took" not in out

    def test_float_drift_just_under_a_full_turn_is_equivalent(self, monkeypatch):
        # 719.9995 deg commanded to 0: the raw difference is 359.9995, so only the 360-d half of
        # min(d, 360-d) recognizes the equivalence - a plain d <= tol test would call this pose a
        # failed drive, on exactly the value float drift produces.
        j = FakeJoint("Crank", _frozen_revolute_at(719.9995))
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=0))
        assert out["equivalent_pose"] is True
        assert "drive_took" not in out and "DID NOT TAKE" not in out["note"]

    def test_genuine_no_take_is_still_flagged(self, monkeypatch):
        # 25 deg commanded against a chain frozen at 0 is NOT equivalent - the failure diagnosis
        # must survive the modulo test, and a no-take is an ERROR.
        j = FakeJoint("Crank", _frozen_revolute_at(0))
        _install(monkeypatch, j)
        res = jd.handler(joint_name="Crank", angle_deg=25)
        assert res["isError"] is True and "DID NOT TAKE" in res["message"]

    def test_a_multi_turn_readback_carries_its_normalized_twin(self, monkeypatch):
        # a drive that TOOK to 450 deg reads back both forms: 450 as stored, 90 normalized.
        j = FakeJoint("Crank", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=450))
        assert out["value_now"]["angle_deg"] == 450.0
        assert out["value_now"]["angle_deg_normalized"] == 90.0
        assert "equivalent_pose" not in out                  # it moved; nothing to explain

    def test_negative_angle_normalizes_into_0_360(self, monkeypatch):
        j = FakeJoint("Crank", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=-30))
        assert out["value_now"]["angle_deg"] == -30.0
        assert out["value_now"]["angle_deg_normalized"] == 330.0

    def test_in_range_readback_has_no_normalized_twin(self, monkeypatch):
        # 25 deg IS its own normalized form - the twin would be noise.
        j = FakeJoint("Crank", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=25))
        assert "angle_deg_normalized" not in out["value_now"]

    def test_a_moving_drive_that_lands_pose_equivalent_reports_the_move(self, monkeypatch):
        # a joint that answers a command by turning BY the delta reads back a value that changed
        # (2160 -> 2250) while matching the command modulo 360: the receipt must report the move,
        # never "the pose already matches", which claims nothing moved.
        class RevoluteJointMotion:                  # the NAME is what current_joint_type keys on
            def __init__(self):
                self.rotationLimits = FakeLimits()
                self._val = math.radians(2160)
            @property
            def rotationValue(self):
                return self._val
            @rotationValue.setter
            def rotationValue(self, v):
                self._val += (v - self._val) % (2 * math.pi)   # move BY the normalized delta
        j = FakeJoint("Crank", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=90))
        assert "equivalent_pose" not in out and "drive_took" not in out
        assert out["value_now"]["angle_deg"] == 2250.0
        assert out["value_now"]["angle_deg_normalized"] == 90.0
        assert "moved the mechanism" in out["note"]

    def test_unreadable_before_value_keeps_the_claim_hedged(self, monkeypatch):
        # the pre-drive read failing means moved-vs-not is unknowable: the receipt says the pose
        # is equivalent AND that whether the mechanism moved is not known - never the confident
        # "nothing needed to move".
        class RevoluteJointMotion:
            def __init__(self):
                self.rotationLimits = FakeLimits()
                self._reads = 0
            @property
            def rotationValue(self):
                self._reads += 1
                if self._reads == 1:
                    raise RuntimeError("transient read failure")
                return math.radians(720)
            @rotationValue.setter
            def rotationValue(self, v):
                pass
        j = FakeJoint("Crank", RevoluteJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Crank", angle_deg=0))
        assert out["equivalent_pose"] is True
        assert "not known from this receipt" in out["note"]
        assert "nothing needed to move" not in out["note"]


# ── which member the drive displaced ────────────────────────────────────────
# The drive reports the member whose placement actually changed, sampled either side of the
# assignment - an observation, never a prediction from the joint's member ordering.

class TestMovedMember:
    def test_the_first_member_is_named_when_it_is_the_one_that_moves(self, monkeypatch):
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm), occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert out["moved"]["occurrence"] == "Arm:1"
        assert out["moved"]["delta_mm"] == [50.0, 0.0, 0.0]
        assert "also_moved" not in out

    def test_the_second_member_is_named_when_it_is_the_one_that_moves(self, monkeypatch):
        # the anchored-first-side case: the same joint, the same command, the OTHER part displaced -
        # naming occurrenceOne from the member ordering alone would report the wrong part here.
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(base, axis=(-1.0, 0.0, 0.0)),
                      occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert out["moved"]["occurrence"] == "Base:1"
        assert out["moved"]["delta_mm"] == [-50.0, 0.0, 0.0]

    def test_both_members_moving_publishes_the_larger_first(self, monkeypatch):
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm, axis=(0.2, 0.0, 0.0),
                                             also=(base, (-1.0, 0.0, 0.0))),
                      occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=10, units="mm"))
        assert out["moved"]["occurrence"] == "Base:1"          # -10 mm, the larger move
        assert out["also_moved"]["occurrence"] == "Arm:1"      # +2 mm
        assert out["also_moved"]["delta_mm"] == [2.0, 0.0, 0.0]

    def test_a_rotation_is_reported_in_degrees(self, monkeypatch):
        rotor, base = _Placed("Rotor:1"), _Placed("Base:1")
        j = FakeJoint("Pivot", _revolute_moving(rotor), occ_one=rotor, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=90))
        assert out["moved"]["occurrence"] == "Rotor:1"
        assert out["moved"]["delta_deg"] == 90.0
        assert out["moved"]["delta_mm"] == [0.0, 0.0, 0.0]     # a spin in place moves no origin

    def test_a_drive_that_displaced_nothing_publishes_moved_null(self, monkeypatch):
        # both placements READ and both unchanged: the honest answer is "nothing moved", which is a
        # different statement from "the placement could not be measured".
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm, axis=(0.0, 0.0, 0.0)),
                      occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert out["moved"] is None
        assert "neither member's placement changed" in out["note"]

    def test_an_unreadable_placement_publishes_no_moved_key_at_all(self, monkeypatch):
        # a null 'moved' would claim nothing moved; these occurrences carry no transform2 at all.
        j = FakeJoint("Rail", SliderJointMotion(), occ_one=_plain_occ(), occ_two=_plain_occ())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert "moved" not in out
        assert "neither member's placement could be read" in out["note"]

    def test_a_move_exactly_at_the_band_is_not_a_move(self, monkeypatch):
        # 0.001 mm is the band itself - only a move BEYOND it counts, or solver noise reads as motion.
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm), occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=0.001, units="mm"))
        assert out["moved"] is None

    def test_a_move_past_the_band_is_reported(self, monkeypatch):
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm), occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=0.002, units="mm"))
        assert out["moved"]["occurrence"] == "Arm:1"
        assert out["moved"]["delta_mm"] == [0.002, 0.0, 0.0]


class TestMovedBandBoundary:
    """The band gate itself, over crafted samples - a basis rounded the way the placement record
    rounds it cannot express a rotation this small, so the degree edge is exercised directly."""
    def _samples(self, monkeypatch, deg):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        before = {"origin": [0.0, 0.0, 0.0], "x_axis": [1.0, 0.0, 0.0],
                  "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0]}
        after = {"origin": [0.0, 0.0, 0.0], "x_axis": [c, s, 0.0],
                 "y_axis": [-s, c, 0.0], "z_axis": [0.0, 0.0, 1.0]}
        monkeypatch.setattr(jd, "_placement", lambda occ: after)
        return [(types.SimpleNamespace(fullPathName="Rotor:1"), before)]

    def test_a_rotation_exactly_at_the_band_is_not_a_move(self, monkeypatch):
        rows, readable = jd._moved_rows(self._samples(monkeypatch, 0.01))
        assert rows == [] and readable is True

    def test_a_rotation_past_the_band_is_a_move(self, monkeypatch):
        rows, readable = jd._moved_rows(self._samples(monkeypatch, 0.011))
        assert readable is True
        assert rows[0]["occurrence"] == "Rotor:1" and rows[0]["delta_deg"] == 0.011

    def test_a_rounded_basis_that_did_not_turn_reports_no_rotation(self, monkeypatch):
        # the axes the placement record publishes are rounded to 4 decimals and so fall short of unit
        # length; comparing them as-is turns that missing length into rotation (measured live: a part
        # that had not turned reported 0.5375 deg, which would name a static member as the mover).
        rounded = {"origin": [0.0, 0.0, 0.0], "x_axis": [0.866, 0.5, 0.0],
                   "y_axis": [-0.5, 0.866, 0.0], "z_axis": [0.0, 0.0, 1.0]}
        assert jd._delta_deg(rounded, rounded) == 0.0
        monkeypatch.setattr(jd, "_placement", lambda occ: dict(rounded))
        rows, readable = jd._moved_rows([(types.SimpleNamespace(fullPathName="Rotor:1"), rounded)])
        assert rows == [] and readable is True


class TestDriveDirection:
    def test_the_slide_direction_is_the_vector_read_BEFORE_the_drive(self, monkeypatch):
        # the drive itself can re-aim the vector; a receipt that re-read it afterwards would publish
        # a direction the reported delta was never measured against.
        arm, base = _Placed("Arm:1"), _Placed("Base:1")
        j = FakeJoint("Rail", _slider_moving(arm, vector=(1.0, 0.0, 0.0),
                                             turn_vector=(0.0, 1.0, 0.0)),
                      occ_one=arm, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert out["slide_direction"] == [1.0, 0.0, 0.0]
        assert "rotation_axis" not in out                  # no angle was commanded

    def test_an_angle_drive_publishes_the_rotation_axis(self, monkeypatch):
        rotor, base = _Placed("Rotor:1"), _Placed("Base:1")
        j = FakeJoint("Pivot", _revolute_moving(rotor, vector=(0.0, 0.0, 1.0)),
                      occ_one=rotor, occ_two=base)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Pivot", angle_deg=30))
        assert out["rotation_axis"] == [0.0, 0.0, 1.0]
        assert "slide_direction" not in out

    def test_a_cylindrical_drive_publishes_both_vectors(self, monkeypatch):
        m = CylindricalJointMotion()
        m.slideDirectionVector = _Vec(0.0, 0.0, 1.0)
        m.rotationAxisVector = _Vec(0.0, 0.0, 1.0)
        j = FakeJoint("Cyl", m)
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Cyl", angle_deg=30, distance=10, units="mm"))
        assert out["slide_direction"] == [0.0, 0.0, 1.0]
        assert out["rotation_axis"] == [0.0, 0.0, 1.0]

    def test_an_unreadable_vector_publishes_no_direction(self, monkeypatch):
        # the stock SliderJointMotion fake carries no slideDirectionVector - an unread vector is
        # absent, never substituted with an axis the joint never reported.
        j = FakeJoint("Rail", SliderJointMotion())
        _install(monkeypatch, j)
        out = _payload(jd.handler(joint_name="Rail", distance=50, units="mm"))
        assert "slide_direction" not in out
        assert "motion vector" not in out["note"]
