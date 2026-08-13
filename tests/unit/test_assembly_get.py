"""Unit tests for ``assembly_get.py`` — structured kinematic state of an assembly.

This is the read tool that lets the agent reason about grounding/position/joint-wiring from NUMBERS
instead of a cluttered screenshot. Pinned: units scaling on positions, the joint-type -> friendly +
DOF mapping, per-occurrence ground flags + bbox, joint connection records, and the
occurrence<->joint cross-index.
"""

import json
import math
from types import SimpleNamespace

import pytest

from conftest import load_tool

ap = load_tool("assembly_get")


class _Pt:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _BBox:
    def __init__(self, mn, mx):
        self.minPoint = _Pt(*mn); self.maxPoint = _Pt(*mx)


class _Trans:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _Matrix:
    """Stands in for the occurrence's transform2 Matrix3D. getAsCoordinateSystem returns
    (origin, xAxis, yAxis, zAxis) - the Python shape of the void-return + 4-output-param API. With no
    basis configured it raises, mimicking a matrix whose coordinate system can't be read (axes omitted)."""
    def __init__(self, origin, basis=None):
        self.translation = _Trans(*origin)
        self._basis = basis

    def getAsCoordinateSystem(self):
        if self._basis is None:
            raise RuntimeError("no coordinate system available")
        x, y, z = self._basis
        return (_Trans(0, 0, 0), _Trans(*x), _Trans(*y), _Trans(*z))


class FakeOcc:
    def __init__(self, name, comp, origin=(0, 0, 0), bbox=None, body_bbox=None,
                 grounded=False, ground_to_parent=False, body_count=1, basis=None,
                 full_path=None):
        self.name = name
        # fullPathName is the ONLY thing that tells two nested instances sharing a leaf name apart;
        # a top-level occurrence's path is just its name.
        self.fullPathName = full_path or name
        self.component = type("C", (), {"name": comp})()
        self.transform2 = _Matrix(origin, basis)
        self.boundingBox = _BBox(*bbox) if bbox else None
        # body_bbox models the bodies-only boundingBox2(entityTypes) read; "empty" = no bodies (None).
        # A FakeOcc WITHOUT body_bbox has no boundingBox2 attr - body_aabb then falls back to
        # .boundingBox, like a BRepBody.
        if body_bbox is not None:
            self.boundingBox2 = (lambda types, bb=body_bbox:
                                 None if bb == "empty" else _BBox(*bb))
        self.isGrounded = grounded
        self.isGroundToParent = ground_to_parent
        self.bRepBodies = type("B", (), {"count": body_count})()


class _Vec:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _Motion:
    """A JointMotion carrying ONLY the value attributes its own class exposes: rotationValue
    (radians) on a revolute, slideValue (cm) on a slider, rotationValue + primarySlideValue +
    secondarySlideValue on a planar, none at all on a rigid."""
    def __init__(self, joint_type, **values):
        self.jointType = joint_type
        for key, value in values.items():
            setattr(self, key, value)


class _UnreadableMotion(_Motion):
    """A motion whose value read raises - unknown, which is not a zero."""

    @property
    def rotationValue(self):
        raise RuntimeError("3 : the value cannot be read")


class _JointFrame:
    """A joint's geometryOrOriginOne/Two: the joint frame in WORLD coordinates - origin (cm) plus
    primaryAxisVector (the frame Z), secondaryAxisVector (X) and thirdAxisVector (Y)."""
    def __init__(self, origin=(0.0, 0.0, 0.0), z=(0, 0, 1), x=(1, 0, 0), y=(0, 1, 0)):
        self.origin = _Vec(*origin)
        self.primaryAxisVector = _Vec(*z)
        self.secondaryAxisVector = _Vec(*x)
        self.thirdAxisVector = _Vec(*y)


class _OpaqueFrame:
    """A geometry reference that EXISTS but answers nothing - every frame read raises. Distinct from
    a null reference, and it must not shadow a readable second geometry."""

    def _unreadable(self):
        raise RuntimeError("3 : the frame cannot be read")

    origin = property(_unreadable)
    primaryAxisVector = property(_unreadable)
    secondaryAxisVector = property(_unreadable)
    thirdAxisVector = property(_unreadable)


class FakeJoint:
    def __init__(self, name, motion_type, occ1, occ2, health_state=0, message="",
                 motion_values=None, motion_cls=_Motion, frame_one=None, frame_two=None):
        self.name = name
        self.jointMotion = motion_cls(motion_type, **(motion_values or {}))
        self.occurrenceOne = type("O", (), {"name": occ1})() if occ1 else None
        self.occurrenceTwo = type("O", (), {"name": occ2})() if occ2 else None
        self.healthState = health_state          # 0 = healthy, non-zero = error/warning
        self.errorOrWarningMessage = message
        # Both geometry references exist and read null on an inferred joint - that null is what the
        # frame read falls back over, so the fakes model the attribute, not its absence.
        self.geometryOrOriginOne = frame_one
        self.geometryOrOriginTwo = frame_two


class FakeTimelineObj:
    def __init__(self, name, health_state=0, message=""):
        self.name = name
        self.healthState = health_state
        self.errorOrWarningMessage = message


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class _NamedBody:
    def __init__(self, name): self.name = name


class FakeRoot:
    def __init__(self, occs, joints, root_bodies=(), asbuilt=(), all_occs=None):
        self.occurrences = _Coll(occs)
        # allOccurrences is a plain list on the ROOT component and is the only walk that reaches
        # NESTED occurrences (root.occurrences is top-level only).
        self.allOccurrences = list(occs) if all_occs is None else list(all_occs)
        self.joints = _Coll(joints)
        self.asBuiltJoints = _Coll(asbuilt)
        self.bRepBodies = _Coll([_NamedBody(n) for n in root_bodies])


class FakeDesign:
    def __init__(self, occs, joints, timeline=None, root_bodies=(), asbuilt=(), marker=None,
                 all_occs=None):
        self.rootComponent = FakeRoot(occs, joints, root_bodies, asbuilt, all_occs)
        self.timeline = _Coll(timeline or [])
        if marker is not None:                 # marker < count = a rolled-back timeline
            self.timeline.markerPosition = marker


def _install(occs, joints, timeline=None, root_bodies=(), asbuilt=(), marker=None):
    design = FakeDesign(occs, joints, timeline, root_bodies, asbuilt, marker)
    ap.app = type("A", (), {"activeProduct": design})()
    ap._common.app = ap.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_units(self):
        _install([], [])
        res = ap.handler(units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]


class TestRolledBackTimeline:
    """A rolled-back marker (markerPosition < count) means features after it - downstream joints
    included - are reverted to home while still reading healthy; assembly_get must surface that and
    NOT report is_healthy over an incomplete model (the state a joint_edit that fails to restore
    the marker leaves behind)."""

    def test_rolled_back_marker_is_incomplete_and_unhealthy(self):
        _install([FakeOcc("A:1", "A")],
                 [FakeJoint("J1", 1, "A:1", "B:1")],
                 timeline=[FakeTimelineObj("J1", 0), FakeTimelineObj("Pattern1", 0)], marker=1)
        out = _payload(ap.handler())
        assert out["timeline_rolled_back"] is True
        assert out["is_healthy"] is False              # would be True without the rolled-back guard
        assert "ROLLED BACK" in out["note"]

    def test_marker_at_end_is_not_rolled_back(self):
        _install([FakeOcc("A:1", "A")], [FakeJoint("J1", 1, "A:1", "B:1")],
                 timeline=[FakeTimelineObj("J1", 0)], marker=1)
        out = _payload(ap.handler())
        assert out["timeline_rolled_back"] is False
        assert out["is_healthy"] is True


class TestProbe:
    def test_reports_root_bodies_not_just_occurrences(self):
        # bodies directly in the root aren't occurrences, so the occurrence loop misses them; the probe
        # must still surface them (they can't be jointed — the user needs to know they exist).
        occ = FakeOcc("Sub:1", "Sub")
        _install([occ], [], root_bodies=["RootBlock"])
        out = _payload(ap.handler())
        assert out["root_bodies"] == ["RootBlock"]
        assert "can't be jointed" in out["note"]

    def test_no_root_bodies_is_empty_and_no_note(self):
        _install([FakeOcc("Sub:1", "Sub")], [])
        out = _payload(ap.handler())
        assert out["root_bodies"] == []
        assert "root_bodies lists geometry" not in out["note"]   # note clause only when non-empty

    def test_positions_scaled_to_display_units(self):
        # origin in cm -> reported in mm
        occ = FakeOcc("Block:1", "Block", origin=(2.0, 0.0, 0.0),
                      bbox=((-1, -1, -1), (1, 1, 1)))
        _install([occ], [])
        out = _payload(ap.handler(units="mm"))
        o = out["occurrences"][0]
        assert o["origin"] == [20.0, 0.0, 0.0]          # 2cm -> 20mm
        assert o["bbox_center"] == [0.0, 0.0, 0.0]
        assert o["bbox_size"] == [20.0, 20.0, 20.0]

    def test_bbox_is_bodies_only_not_the_sketch_inflated_box(self):
        # occ.boundingBox also counts visible sketches/construction datums - live-verified: an
        # orphaned oversized sketch made a 68x10x10 body read 120x120x10 centered off the part. The
        # bbox must come from the bodies-only boundingBox2 read, NEVER the polluted plain box.
        occ = FakeOcc("Shaft:1", "Shaft",
                      bbox=((-8, -3, 0), (4, 9, 1)),            # sketch-polluted 120x120x10 (cm/10)
                      body_bbox=((-3.4, -0.5, 0), (3.4, 0.5, 1)))  # the body's true 68x10x10 mm
        _install([occ], [])
        o = _payload(ap.handler(units="mm"))["occurrences"][0]
        assert o["bbox_size"] == [68.0, 10.0, 10.0]
        assert o["bbox_center"] == [0.0, 0.0, 5.0]

    def test_bbox_omitted_when_occurrence_has_no_body_geometry(self):
        # no bodies -> boundingBox2 returns None. The bbox is OMITTED - falling back to the plain
        # .boundingBox here would report a box made purely of sketches/datums.
        occ = FakeOcc("Empty:1", "Empty", bbox=((-6, -6, 0), (6, 6, 0)), body_bbox="empty",
                      body_count=0)
        _install([occ], [])
        o = _payload(ap.handler())["occurrences"][0]
        assert "bbox_size" not in o and "bbox_center" not in o

    def test_ground_flags_and_grounded_list(self):
        block = FakeOcc("Block:1", "Block", grounded=True, ground_to_parent=True)
        crank = FakeOcc("Crank:1", "Crank", grounded=False, ground_to_parent=False)
        _install([block, crank], [])
        out = _payload(ap.handler())
        assert out["grounded_occurrences"] == ["Block:1"]
        bycomp = {o["name"]: o for o in out["occurrences"]}
        assert bycomp["Crank:1"]["ground_to_parent"] is False

    def test_joint_type_and_dof_mapping(self):
        _install([FakeOcc("A:1", "A"), FakeOcc("B:1", "B")],
                 [FakeJoint("CrankMain", 1, "A:1", "B:1")])   # 1 = revolute
        out = _payload(ap.handler())
        j = out["joints"][0]
        assert j["type"] == "revolute" and j["dof"] == 1
        assert j["occurrence_one"] == "A:1" and j["occurrence_two"] == "B:1"

    def test_rigid_and_cylindrical_dof(self):
        _install([], [FakeJoint("R", 0, "A:1", "B:1"), FakeJoint("C", 3, "A:1", "B:1")])
        out = _payload(ap.handler())
        by = {x["name"]: x for x in out["joints"]}
        assert by["R"]["type"] == "rigid" and by["R"]["dof"] == 0
        assert by["C"]["type"] == "cylindrical" and by["C"]["dof"] == 2

    def test_all_motion_types_and_dof(self):
        # pin_slot(4)=2dof, planar(5)=3dof, ball(6)=3dof, slider(2)=1dof — the remaining _MOTION rows.
        _install([], [FakeJoint("Slide", 2, "A:1", "B:1"),
                      FakeJoint("PinSlot", 4, "A:1", "B:1"),
                      FakeJoint("Planar", 5, "A:1", "B:1"),
                      FakeJoint("Ball", 6, "A:1", "B:1")])
        by = {x["name"]: x for x in _payload(ap.handler())["joints"]}
        assert by["Slide"]["type"] == "slider" and by["Slide"]["dof"] == 1
        assert by["PinSlot"]["type"] == "pin_slot" and by["PinSlot"]["dof"] == 2
        assert by["Planar"]["type"] == "planar" and by["Planar"]["dof"] == 3
        assert by["Ball"]["type"] == "ball" and by["Ball"]["dof"] == 3

    def test_unknown_motion_type_is_question_mark_with_null_dof(self):
        _install([], [FakeJoint("Mystery", 99, "A:1", "B:1")])
        j = _payload(ap.handler())["joints"][0]
        assert j["type"] == "?" and j["dof"] is None

    def test_positions_scaled_to_cm_and_inch(self):
        # same 2cm origin reported in cm (unchanged) and in inches (2cm / 2.54).
        occ = FakeOcc("Block:1", "Block", origin=(2.54, 0.0, 0.0))
        _install([occ], [])
        cm = _payload(ap.handler(units="cm"))["occurrences"][0]
        assert cm["origin"] == [2.54, 0.0, 0.0]
        inch = _payload(ap.handler(units="in"))["occurrences"][0]
        assert inch["origin"] == [1.0, 0.0, 0.0]    # 2.54 cm -> 1 inch

    def test_occurrence_joint_cross_index(self):
        _install([FakeOcc("Crank:1", "Crank"), FakeOcc("Block:1", "Block")],
                 [FakeJoint("CrankMain", 1, "Crank:1", "Block:1")])
        out = _payload(ap.handler())
        by = {o["name"]: o for o in out["occurrences"]}
        assert by["Crank:1"]["joints"] == ["CrankMain"]
        assert by["Block:1"]["joints"] == ["CrankMain"]

    def test_include_joints_false_skips(self):
        _install([FakeOcc("A:1", "A")], [FakeJoint("J", 1, "A:1", None)])
        out = _payload(ap.handler(include_joints=False))
        assert out["joints"] is None
        assert "joints" not in out["occurrences"][0]

    def test_include_joints_false_still_counts_and_reports_broken(self):
        # include_joints gates EMISSION only - joint_count and broken_joints come from the always-run
        # walk (with the walk skipped they read 0/[] while joints existed, live-observed).
        _install([FakeOcc("A:1", "A")],
                 [FakeJoint("Good", 1, "A:1", None), FakeJoint("Bad", 1, "A:1", None, health_state=2)])
        out = _payload(ap.handler(include_joints=False))
        assert out["joint_count"] == 2
        assert out["broken_joints"] == ["Bad"]
        assert out["is_healthy"] is False

    def test_as_built_joints_are_visible(self):
        # as-built joints live in root.asBuiltJoints, a SEPARATE collection from root.joints. The probe
        # must read both, or a script-created as-built joint is invisible (joint_count undercounts and
        # the occurrence cross-index misses it). AsBuiltJoint exposes the same name/jointMotion/
        # occurrenceOne/Two surface, so FakeJoint stands in.
        _install([FakeOcc("Ring:1", "Ring"), FakeOcc("Rotor:1", "Rotor")],
                 [FakeJoint("RegularPin", 1, "Ring:1", "Rotor:1")],
                 asbuilt=[FakeJoint("AsBuiltSpin", 1, "Rotor:1", "Ring:1")])
        out = _payload(ap.handler())
        by = {j["name"]: j for j in out["joints"]}
        assert "AsBuiltSpin" in by and by["AsBuiltSpin"]["type"] == "revolute"
        assert out["joint_count"] == 2                       # both collections counted
        occ = {o["name"]: o for o in out["occurrences"]}
        assert "AsBuiltSpin" in occ["Rotor:1"]["joints"]     # cross-indexed like any joint

    def test_broken_as_built_joint_breaks_health(self):
        # an as-built joint that failed to compute must drop is_healthy, same as a regular joint.
        _install([], [], asbuilt=[FakeJoint("AB", 1, "A:1", "B:1", health_state=1, message="conflict")])
        out = _payload(ap.handler())
        assert out["is_healthy"] is False and out["broken_joints"] == ["AB"]


# ── ORIENTATION: the occurrence's rotation as three world basis axes ────────────────────────────
# The per-occurrence record reports rotation as x_axis/y_axis/z_axis unit vectors read from transform2
# via getAsCoordinateSystem. An unrotated occurrence reads identity; axes are omitted (never faked)
# when the coordinate system can't be read.

class TestOrientation:
    def test_identity_rotation_reads_axis_aligned_basis(self):
        occ = FakeOcc("Block:1", "Block",
                      basis=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        _install([occ], [])
        o = _payload(ap.handler())["occurrences"][0]
        assert o["x_axis"] == [1.0, 0.0, 0.0]
        assert o["y_axis"] == [0.0, 1.0, 0.0]
        assert o["z_axis"] == [0.0, 0.0, 1.0]

    def test_90deg_z_rotation_basis(self):
        # a +90 deg rotation about Z: x->+Y, y->-X, z unchanged.
        occ = FakeOcc("Crank:1", "Crank",
                      basis=((0, 1, 0), (-1, 0, 0), (0, 0, 1)))
        _install([occ], [])
        o = _payload(ap.handler())["occurrences"][0]
        assert o["x_axis"] == [0.0, 1.0, 0.0]
        assert o["y_axis"] == [-1.0, 0.0, 0.0]
        assert o["z_axis"] == [0.0, 0.0, 1.0]

    def test_axes_omitted_when_coordinate_system_unavailable(self):
        # no basis configured -> getAsCoordinateSystem raises -> axes omitted, origin still reported.
        occ = FakeOcc("Plain:1", "Plain", origin=(1.0, 0.0, 0.0))
        _install([occ], [])
        o = _payload(ap.handler(units="cm"))["occurrences"][0]
        assert "x_axis" not in o and "y_axis" not in o and "z_axis" not in o
        assert o["origin"] == [1.0, 0.0, 0.0]


# ── HEALTH: the thing a user sees FIRST (Compute Failed) ───────────────────────────────────────
#
# A joint can be created + wired correctly yet FAIL TO COMPUTE (mis-axised -> over-constrained).
# The probe must surface that (is_healthy / broken_joints / per-joint healthy + timeline_problems)
# so it never reports a broken assembly as fine - observed live: a joint reads healthState=1
# (Compute Failed) while every structural read looks correct.

class TestHealth:
    def test_all_healthy(self):
        _install([], [FakeJoint("J1", 1, "A:1", "B:1"), FakeJoint("J2", 2, "A:1", "B:1")])
        out = _payload(ap.handler())
        assert out["is_healthy"] is True
        assert out["broken_joints"] == []
        assert all(j["healthy"] is True for j in out["joints"])

    def test_broken_joint_surfaced(self):
        # one joint failed to compute (healthState 1) -> probe must flag it
        _install([], [
            FakeJoint("Good", 1, "A:1", "B:1"),
            FakeJoint("PistonSlide1", 2, "P:1", "B:1", health_state=1,
                      message="Can't resolve some component positions because there are conflicts."),
        ])
        out = _payload(ap.handler())
        assert out["is_healthy"] is False
        assert out["broken_joints"] == ["PistonSlide1"]
        by = {j["name"]: j for j in out["joints"]}
        assert by["PistonSlide1"]["healthy"] is False
        assert "conflicts" in by["PistonSlide1"]["error"]
        assert by["Good"]["healthy"] is True

    def test_suppressed_joint_is_not_broken(self):
        # healthState 3 = SUPPRESSED (an author-parked alternate, e.g. a fixture template's reversed jaw)
        # - it is INTENTIONAL, not a compute failure. Must report healthy=True and NOT drop is_healthy.
        # (Live case: 'Jaw to Y+ Stock REVERSED' healthState=3 isValid=True in a CAM template.)
        _install([], [FakeJoint("Active", 1, "A:1", "B:1"),
                      FakeJoint("Parked REVERSED", 1, "A:1", "B:1", health_state=3)])
        out = _payload(ap.handler())
        assert out["is_healthy"] is True                 # suppression is not breakage
        assert out["broken_joints"] == []
        by = {j["name"]: j for j in out["joints"]}
        assert by["Parked REVERSED"]["healthy"] is True

    def test_unknown_rollup_state_is_not_a_problem(self):
        # a collapsed TimelineGroup (Fusion wraps one around an inserted component) reports a rollup
        # healthState that is neither healthy/suppressed nor a real warning(1)/error(2). It is NOT a
        # compute failure - _common.timeline_health ignores it, so the probe must too, or is_healthy
        # disagrees with design_get on the same design (a false 'compute failed' alarm).
        _install([], [], timeline=[FakeTimelineObj("Group1", health_state=4)])
        out = _payload(ap.handler())
        assert out["is_healthy"] is True
        assert out["timeline_problems"] == []

    def test_stale_joint_health_flagged_when_timeline_is_clean(self):
        # per-joint healthState LAGS the timeline after an in-place edit. When a joint reads broken but
        # the timeline shows NO errored feature, flag potential staleness + point to design_recompute.
        _install([], [FakeJoint("Wheel_Spin", 1, "W:1", "A:1", health_state=2)],
                 timeline=[FakeTimelineObj("Joint1", 0)])   # timeline CLEAN
        out = _payload(ap.handler())
        assert out["broken_joints"] == ["Wheel_Spin"]
        assert out.get("health_may_be_stale") is True
        assert "design_recompute" in out["note"]

    def test_no_stale_flag_when_timeline_also_shows_the_error(self):
        # genuine breakage (joint AND timeline agree) -> NOT flagged as stale
        _install([], [FakeJoint("J", 1, "A:1", "B:1", health_state=2)],
                 timeline=[FakeTimelineObj("J", 1, message="broke")])
        out = _payload(ap.handler())
        assert out.get("health_may_be_stale") is None

    def test_timeline_problem_surfaced(self):
        _install([], [],
                 timeline=[FakeTimelineObj("Extrude5", 0),
                           FakeTimelineObj("Fillet1", 1, message="The fillet failed.")])
        out = _payload(ap.handler())
        assert out["is_healthy"] is False
        probs = {p["name"]: p for p in out["timeline_problems"]}
        assert "Fillet1" in probs and "Extrude5" not in probs

    def test_health_message_deduped(self):
        # Fusion repeats the message + appends "Compute Failed<name>"; we keep the first chunk.
        msg = ("Can't resolve positions.\n\nInspect relationships.Compute FailedXCan't resolve "
               "positions.Compute FailedX")
        _install([], [FakeJoint("X", 2, "A:1", "B:1", health_state=1, message=msg)])
        out = _payload(ap.handler())
        err = out["joints"][0]["error"]
        assert "Compute Failed" not in err and "Can't resolve positions" in err


# ── BOUNDED READS: occurrences/joints arrays cap + report truncated (CLAUDE.md "Bound it") ──────

class TestCaps:
    def test_occurrences_under_cap_untruncated_and_unchanged(self):
        occs = [FakeOcc(f"O{i}:1", f"C{i}") for i in range(5)]
        _install(occs, [])
        out = _payload(ap.handler())
        assert out["occurrences_truncated"] is False
        assert len(out["occurrences"]) == 5
        assert out["occurrence_count"] == 5

    def test_occurrences_at_cap_truncates_and_flags(self):
        occs = [FakeOcc(f"O{i}:1", f"C{i}") for i in range(60)]
        _install(occs, [])
        out = _payload(ap.handler(max_occurrences=50))
        assert out["occurrences_truncated"] is True
        assert len(out["occurrences"]) == 50
        # the full count is still honest, even though the array is capped
        assert out["occurrence_count"] == 60

    def test_joints_under_cap_untruncated_and_unchanged(self):
        joints = [FakeJoint(f"J{i}", 1, "A:1", "B:1") for i in range(5)]
        _install([], joints)
        out = _payload(ap.handler())
        assert out["joints_truncated"] is False
        assert len(out["joints"]) == 5
        assert out["joint_count"] == 5

    def test_joints_at_cap_truncates_and_flags(self):
        joints = [FakeJoint(f"J{i}", 1, "A:1", "B:1") for i in range(120)]
        _install([], joints)
        out = _payload(ap.handler(max_joints=100))
        assert out["joints_truncated"] is True
        assert len(out["joints"]) == 100
        # the full count (and health rollup) still sees every joint, even beyond the cap
        assert out["joint_count"] == 120

    def test_default_caps_are_generous_enough_for_a_normal_model(self):
        # the DEFAULT caps (50 occurrences / 100 joints) must not bite a normal small model.
        occs = [FakeOcc(f"O{i}:1", f"C{i}") for i in range(10)]
        joints = [FakeJoint(f"J{i}", 1, "A:1", "B:1") for i in range(10)]
        _install(occs, joints)
        out = _payload(ap.handler())
        assert out["occurrences_truncated"] is False
        assert out["joints_truncated"] is False


# ── include=['joint_origins']: each Joint Origin as a referenceable, handle-bearing row ──────────────
#
# The JOINT-ORIGIN SEAM read. The default omits it (and advertises it); include= adds a row per JO
# INSTANCE: name + qualified reference (bare, or '<occ>:<name>'), owning component, world position +
# frame axes, the joints that CONSUME it, and a handle. A sub-component JO is reported per occurrence.

class _JOPt:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _SliceJO:
    def __init__(self, name, pos=(0.0, 0.0, 0.0), offsets=(0.0, 0.0, 0.0), token=None):
        self.name = name
        self.geometry = SimpleNamespace(origin=_JOPt(*pos))   # the BASE anchor point (cm)
        self.primaryAxisVector = _JOPt(0.0, 0.0, 1.0)     # Z
        self.secondaryAxisVector = _JOPt(1.0, 0.0, 0.0)   # X
        self.thirdAxisVector = _JOPt(0.0, 1.0, 0.0)       # Y
        # offsetX/Y/Z ModelParameters (cm) - a coordinate-anchored JO carries its position here.
        self.offsetX = SimpleNamespace(value=offsets[0])
        self.offsetY = SimpleNamespace(value=offsets[1])
        self.offsetZ = SimpleNamespace(value=offsets[2])
        self.entityToken = token
        self._pos, self._offsets = pos, offsets

    def createForAssemblyContext(self, occ):
        p = _SliceJO(self.name, pos=self._pos, offsets=self._offsets, token=self.entityToken)
        p.context = occ
        return p


class _SliceComp:
    def __init__(self, name, jos=()):
        self.name = name
        self.jointOrigins = _Coll(list(jos))


class _SliceOcc:
    def __init__(self, full, comp):
        self.fullPathName = full
        self.name = full
        self.component = comp


class _SliceJoint:
    """A joint whose geometryOrOriginOne/Two may reference a JointOrigin (drives consumed_by)."""
    def __init__(self, name, origin_one=None, origin_two=None):
        self.name = name
        self.geometryOrOriginOne = origin_one
        self.geometryOrOriginTwo = origin_two
        self.entityToken = "J:" + name


class _SliceRoot:
    def __init__(self, name="Root", jos=(), joints=(), occ_by_comp=None):
        self.name = name
        self.jointOrigins = _Coll(list(jos))
        self.joints = _Coll(list(joints))
        self.asBuiltJoints = _Coll([])
        self._occ_by_comp = occ_by_comp or {}

    def allOccurrencesByComponent(self, comp):
        return self._occ_by_comp.get(getattr(comp, "name", None), [])

    @property
    def allOccurrences(self):
        return [o for lst in self._occ_by_comp.values() for o in lst]


class _SliceDesign:
    def __init__(self, root, subs=()):
        self.rootComponent = root
        self._subs = list(subs)

    @property
    def allComponents(self):
        return [self.rootComponent] + self._subs


def _install_slice(design):
    import adsk.fusion
    adsk.fusion.JointOrigin = _SliceJO
    ap.app = type("A", (), {"activeProduct": design})()
    ap._common.app = ap.app
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _SliceDesign) else None


class TestJointOriginsSlice:
    def test_default_omits_slice_and_advertises_it(self):
        _install_slice(_SliceDesign(_SliceRoot(jos=[_SliceJO("Stock_Center", token="T")])))
        out = _payload(ap.handler())                       # no include
        assert "joint_origins" not in out
        assert "include=['joint_origins']" in out["note"]  # a flag is invisible unless advertised

    def test_unknown_include_errors(self):
        _install_slice(_SliceDesign(_SliceRoot()))
        res = ap.handler(include=["bogus"])
        assert res["isError"] is True and "bogus" in res["message"]

    def test_root_jo_row_has_handle_position_axes_and_bare_name(self):
        jo = _SliceJO("Stock_Center", pos=(0.0, 0.0, 4.5), token="JO_TOKEN")
        _install_slice(_SliceDesign(_SliceRoot(jos=[jo])))
        out = _payload(ap.handler(include=["joint_origins"], units="mm"))
        rows = out["joint_origins"]
        assert len(rows) == 1 and out["joint_origin_count"] == 1
        r = rows[0]
        assert r["name"] == "Stock_Center" and r["qualified_name"] == "Stock_Center"  # bare on root
        assert r["world_position"] == [0.0, 0.0, 45.0]     # 4.5 cm -> 45 mm
        assert r["frame"]["z_axis"] == [0.0, 0.0, 1.0] and r["frame"]["x_axis"] == [1.0, 0.0, 0.0]
        assert r["handle"] == "JO_TOKEN"

    def test_coordinate_jo_world_position_adds_offsets_to_the_base(self):
        # a coordinate-anchored JO holds its position in offsetX/Y/Z (geometry.origin stays at the base,
        # e.g. the model origin). world_position must ADD the offsets along the frame axes - reading
        # geometry.origin alone reports the base (the live bug this closes: a +45mm-Z JO read [0,0,0]).
        jo = _SliceJO("Stock_Center", pos=(0.0, 0.0, 0.0), offsets=(0.0, 0.0, 4.5), token="T")
        _install_slice(_SliceDesign(_SliceRoot(jos=[jo])))
        r = _payload(ap.handler(include=["joint_origins"], units="mm"))["joint_origins"][0]
        assert r["world_position"] == [0.0, 0.0, 45.0]     # base (0,0,0) + offsetZ 4.5cm along +Z

    def test_consumed_by_names_the_joints_that_reference_the_jo(self):
        # the grip joint names the JO via geometryOrOriginOne -> consumed_by lists it.
        stock = _SliceJO("Stock_Center", token="S")
        vise = _SliceJO("Vise_Center", token="V")
        grip = _SliceJoint("Stock_Gripped_By_Vise", origin_one=stock, origin_two=vise)
        _install_slice(_SliceDesign(_SliceRoot(jos=[stock, vise], joints=[grip])))
        rows = {r["name"]: r for r in _payload(ap.handler(include=["joint_origins"]))["joint_origins"]}
        assert rows["Stock_Center"]["consumed_by"] == ["Stock_Gripped_By_Vise"]
        assert rows["Vise_Center"]["consumed_by"] == ["Stock_Gripped_By_Vise"]

    def test_unconsumed_jo_has_empty_consumed_by(self):
        _install_slice(_SliceDesign(_SliceRoot(jos=[_SliceJO("Lonely", token="L")])))
        r = _payload(ap.handler(include=["joint_origins"]))["joint_origins"][0]
        assert r["consumed_by"] == []

    def test_subcomponent_jo_reported_per_occurrence_with_qualified_name(self):
        native = _SliceJO("Center", pos=(1.0, 0.0, 0.0), token="C")
        sub = _SliceComp("Tower", jos=[native])
        occ = _SliceOcc("Tower:1", sub)
        root = _SliceRoot(jos=[], occ_by_comp={"Tower": [occ]})
        _install_slice(_SliceDesign(root, subs=[sub]))
        rows = _payload(ap.handler(include=["joint_origins"]))["joint_origins"]
        assert len(rows) == 1
        assert rows[0]["qualified_name"] == "Tower:1:Center"     # the resolver-accepted form
        assert rows[0]["component"] == "Tower"

    def test_joint_origins_cap_and_truncated(self):
        jos = [_SliceJO(f"JO{i}", token=f"T{i}") for i in range(5)]
        _install_slice(_SliceDesign(_SliceRoot(jos=jos)))
        out = _payload(ap.handler(include=["joint_origins"], max_joint_origins=3))
        assert len(out["joint_origins"]) == 3
        assert out["joint_origin_count"] == 5 and out["joint_origins_truncated"] is True


# ── include=['relations']: the maintained relationships that are NOT joints ──────────────────────
#
# Rigid groups, motion links and assembly constraints live in three collections the joint walk never
# sees, so without this slice they are invisible to a reader and un-addressable by
# assembly_edit_relations. The default omits it (and advertises it); include= adds a row per
# relation carrying the name the editor resolves by and the state an edit would change.

class _RelRigid:
    def __init__(self, name, members=(), suppressed=False, token=None):
        self.name = name
        self.entityToken = token or f"RG:{name}"
        self.isSuppressed = suppressed
        self.occurrences = _Coll([SimpleNamespace(fullPathName=m, name=m.split("+")[-1])
                                  for m in members])


class _RelLink:
    def __init__(self, name, one="CrankAxis", two="Spin", values=(1.0, 2.0), reversed_=False,
                 suppressed=False, health=0, message="", token=None):
        self.name = name
        self.entityToken = token or f"ML:{name}"
        self.jointOne = SimpleNamespace(name=one) if one else None
        self.jointTwo = SimpleNamespace(name=two) if two else None
        self.valueOne = SimpleNamespace(value=values[0])
        self.valueTwo = SimpleNamespace(value=values[1])
        self.isReversed = reversed_
        self.isSuppressed = suppressed
        self.healthState = health
        self.errorOrWarningMessage = message


class _RelConstraint:
    def __init__(self, name, relationships=2, suppressed=False, health=0, message="", token=None):
        self.name = name
        self.entityToken = token or f"AC:{name}"
        self.geometricRelationships = _Coll([object()] * relationships)
        self.isSuppressed = suppressed
        self.healthState = health
        self.errorOrWarningMessage = message


class _RelComp:
    def __init__(self, name, rigid=(), links=(), constraints=()):
        self.name = name
        self.rigidGroups = _Coll(list(rigid))
        self.motionLinks = _Coll(list(links))
        self.assemblyConstraints = _Coll(list(constraints))
        self.jointOrigins = _Coll([])
        self.joints = _Coll([])
        self.asBuiltJoints = _Coll([])


@pytest.fixture
def relations_design(monkeypatch):
    """A design whose root carries the given relations (plus optional sub-components), wired into
    assembly_get through a fixture so the patches undo themselves."""
    def _build(rigid=(), links=(), constraints=(), subs=()):
        root = _RelComp("Root", rigid, links, constraints)
        design = _SliceDesign(root, subs=subs)
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ap, "app", fake_app)
        monkeypatch.setattr(ap._common, "app", fake_app)
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, _SliceDesign) else None)
        return design
    return _build


class TestRelationsSlice:
    def test_default_omits_the_slice_and_advertises_it(self, relations_design):
        relations_design(rigid=[_RelRigid("RigidGroup1", members=["Frame:1", "Carrier:1"])])
        out = _payload(ap.handler())
        assert "relations" not in out and "relation_counts" not in out
        assert "include=['relations']" in out["note"]

    def test_empty_design_reports_three_empty_lists(self, relations_design):
        relations_design()
        out = _payload(ap.handler(include=["relations"]))
        assert out["relations"] == {"rigid_groups": [], "motion_links": [], "constraints": []}
        assert out["relation_counts"] == {"rigid_groups": 0, "motion_links": 0, "constraints": 0}
        assert out["relations_truncated"] is False

    def test_rigid_group_row_carries_members_and_suppression(self, relations_design):
        relations_design(rigid=[_RelRigid("RigidGroup1", members=["Frame:1", "Carrier:1"],
                                          suppressed=True)])
        row = _payload(ap.handler(include=["relations"]))["relations"]["rigid_groups"][0]
        assert row["name"] == "RigidGroup1" and row["component"] == "Root"
        assert row["occurrences"] == ["Frame:1", "Carrier:1"] and row["occurrence_count"] == 2
        assert row["suppressed"] is True

    def test_rigid_group_members_are_bounded_but_the_count_is_honest(self, relations_design):
        relations_design(rigid=[_RelRigid("Big", members=[f"P{i}:1" for i in range(30)])])
        out = _payload(ap.handler(include=["relations"]))
        row = out["relations"]["rigid_groups"][0]
        assert len(row["occurrences"]) == 12          # the member preview cap
        assert row["occurrence_count"] == 30          # the truth, uncapped
        # a capped member list must never be SILENT: the row is flagged and the top-level
        # truncation flag counts it, even though the relation LISTS themselves fit the cap.
        assert row["occurrences_truncated"] is True
        assert out["relations_truncated"] is True
        assert "occurrences_truncated" in out["note"]

    def test_an_uncapped_member_list_is_not_flagged(self, relations_design):
        relations_design(rigid=[_RelRigid("Small", members=["P0:1", "P1:1"])])
        out = _payload(ap.handler(include=["relations"]))
        assert out["relations"]["rigid_groups"][0]["occurrences_truncated"] is False
        assert out["relations_truncated"] is False

    def test_motion_link_row_names_both_joints_and_its_values(self, relations_design):
        relations_design(links=[_RelLink("MotionLink1", one="CrankAxis", two="Spin",
                                         values=(1.0, 2.0), reversed_=True)])
        row = _payload(ap.handler(include=["relations"]))["relations"]["motion_links"][0]
        assert row["joint_one"] == "CrankAxis" and row["joint_two"] == "Spin"
        assert row["value_one"] == 1.0 and row["value_two"] == 2.0
        assert row["reversed"] is True and row["healthy"] is True

    def test_same_joint_link_reports_a_null_second_joint(self, relations_design):
        # jointTwo is null when a link couples two DOF of ONE joint - the row must report null
        # rather than drop the link.
        relations_design(links=[_RelLink("SelfLink", two=None)])
        row = _payload(ap.handler(include=["relations"]))["relations"]["motion_links"][0]
        assert row["joint_one"] == "CrankAxis" and row["joint_two"] is None

    def test_a_broken_relation_reports_unhealthy_with_its_message(self, relations_design):
        relations_design(links=[_RelLink("Bad", health=2, message="Compute Failed")],
                         constraints=[_RelConstraint("AC1", health=1, message="over-constrained")])
        rels = _payload(ap.handler(include=["relations"]))["relations"]
        assert rels["motion_links"][0]["healthy"] is False
        assert rels["constraints"][0]["healthy"] is False
        assert "over-constrained" in rels["constraints"][0]["error"]

    def test_constraint_row_counts_its_relationships(self, relations_design):
        relations_design(constraints=[_RelConstraint("AC1", relationships=3, suppressed=True)])
        row = _payload(ap.handler(include=["relations"]))["relations"]["constraints"][0]
        assert row["relationship_count"] == 3 and row["suppressed"] is True

    def test_one_two_and_many_relations_are_all_listed(self, relations_design):
        for n in (1, 2, 6):
            relations_design(rigid=[_RelRigid(f"RG{i}") for i in range(n)])
            out = _payload(ap.handler(include=["relations"]))
            assert out["relation_counts"]["rigid_groups"] == n
            assert len(out["relations"]["rigid_groups"]) == n

    def test_subcomponent_relations_are_listed_with_their_component(self, relations_design):
        # a relation created inside a sub-assembly lives on THAT component; a root-only read would
        # report the design as having none.
        relations_design(subs=[_RelComp("Tower", rigid=[_RelRigid("SubGroup")])])
        rows = _payload(ap.handler(include=["relations"]))["relations"]["rigid_groups"]
        assert [r["name"] for r in rows] == ["SubGroup"] and rows[0]["component"] == "Tower"

    def test_cap_truncates_each_list_and_flags_it(self, relations_design):
        relations_design(rigid=[_RelRigid(f"RG{i}") for i in range(5)],
                         links=[_RelLink(f"ML{i}") for i in range(4)])
        out = _payload(ap.handler(include=["relations"], max_relations=2))
        assert len(out["relations"]["rigid_groups"]) == 2
        assert len(out["relations"]["motion_links"]) == 2
        assert out["relation_counts"] == {"rigid_groups": 5, "motion_links": 4, "constraints": 0}
        assert out["relations_truncated"] is True and "max_relations" in out["note"]

    def test_relations_and_joint_origins_compose(self, relations_design):
        # each include= adds exactly its own slice; asking for both must not drop either.
        relations_design(rigid=[_RelRigid("RG1")])
        out = _payload(ap.handler(include=["relations", "joint_origins"]))
        assert out["relation_counts"]["rigid_groups"] == 1
        assert out["joint_origins"] == [] and out["joint_origin_count"] == 0


# ── include=['contacts']: the design's contact sets + the two contact-analysis flags ─────────────
#
# Contact sets hang off the DESIGN, not a component, so neither the joint walk nor the relations
# walk sees them. The default omits the slice (and advertises it); include= adds a row per set -
# members (previewed), the true member count, suppression - beside the two flags that decide whether
# any of them takes part at all. A ContactSet carries no entityToken and no healthState, so a row
# carries neither a handle nor a healthy key.

class _RawMember:
    """What a BODY member reads back as: an object neither cast accepts, so it counts but has no name."""


class _MemberOcc:
    def __init__(self, path):
        self.fullPathName = path
        self.name = path.split("+")[-1]


class _ContactSetRow:
    def __init__(self, name, members=(), suppressed=False):
        self.name = name
        self.occurencesAndBodies = list(members)      # ONE 'r' - the real property name
        self.isSuppressed = suppressed


class _UnreadableMembers(_ContactSetRow):
    """A set whose member list cannot be read at all - distinct from a set that holds nothing."""

    @property
    def occurencesAndBodies(self):
        raise RuntimeError("3 : the member list cannot be read")

    @occurencesAndBodies.setter
    def occurencesAndBodies(self, value):
        pass


class _UnreadableScopeDesign(_SliceDesign):
    """A design whose isContactSetAnalysis raises - the scope has no answer, which is not all_bodies."""

    @property
    def isContactSetAnalysis(self):
        raise RuntimeError("3 : the flag cannot be read")

    @isContactSetAnalysis.setter
    def isContactSetAnalysis(self, value):
        pass


class _ContactSetsColl:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


@pytest.fixture
def contacts_design(monkeypatch):
    """A design carrying contact sets and both analysis flags, wired into assembly_get through a
    fixture so the patches undo themselves. Occurrence/BRepBody casts decide which members can be
    NAMED - a member that casts to neither is the measured body case."""
    def _build(sets=(), enabled=False, use_sets=False, design_cls=_SliceDesign):
        design = design_cls(_RelComp("Root"))
        design.contactSets = _ContactSetsColl(sets)
        design.isContactAnalysisEnabled = enabled
        design.isContactSetAnalysis = use_sets
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ap, "app", fake_app)
        monkeypatch.setattr(ap._common, "app", fake_app)
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, _SliceDesign) else None)
        monkeypatch.setattr(adsk.fusion, "Occurrence",
                            type("Occurrence", (), {"cast": staticmethod(
                                lambda x: x if isinstance(x, _MemberOcc) else None)}), raising=False)
        monkeypatch.setattr(adsk.fusion, "BRepBody",
                            type("BRepBody", (), {"cast": staticmethod(lambda x: None)}),
                            raising=False)
        return design
    return _build


class TestContactsSlice:
    def test_default_omits_the_slice_and_advertises_it(self, contacts_design):
        contacts_design(sets=[_ContactSetRow("ContactSet1")])
        out = _payload(ap.handler())
        assert "contacts" not in out and "contact_analysis" not in out
        assert "include=['contacts']" in out["note"]

    def test_empty_design_reports_an_empty_list_and_both_flags(self, contacts_design):
        contacts_design()
        out = _payload(ap.handler(include=["contacts"]))
        assert out["contacts"] == [] and out["contact_count"] == 0
        assert out["contacts_truncated"] is False
        assert out["contact_analysis"] == {"enabled": False, "scope": "all_bodies"}

    def test_a_row_carries_members_count_and_suppression(self, contacts_design):
        contacts_design(sets=[_ContactSetRow("Frame_Panel",
                                             members=[_MemberOcc("Frame:1"), _MemberOcc("Panel:1")],
                                             suppressed=True)])
        row = _payload(ap.handler(include=["contacts"]))["contacts"][0]
        assert row["name"] == "Frame_Panel"
        assert row["members"] == ["Frame:1", "Panel:1"] and row["member_count"] == 2
        assert row["members_truncated"] is False and row["suppressed"] is True

    def test_a_row_carries_no_handle_and_no_health(self, contacts_design):
        # a ContactSet has neither entityToken nor healthState - inventing either key would promise
        # a round-trip and a health verdict that do not exist.
        contacts_design(sets=[_ContactSetRow("ContactSet1", members=[_MemberOcc("A:1")])])
        row = _payload(ap.handler(include=["contacts"]))["contacts"][0]
        assert "handle" not in row and "healthy" not in row

    def test_a_body_member_is_counted_but_not_named(self, contacts_design):
        # measured: a body member reads back as a raw object both casts reject. Counting it keeps
        # member_count honest; a names-only row would silently under-report the membership.
        contacts_design(sets=[_ContactSetRow("Mixed", members=[_RawMember(), _MemberOcc("A:1")])])
        row = _payload(ap.handler(include=["contacts"]))["contacts"][0]
        assert row["member_count"] == 2
        assert row["members"] == ["A:1"] and row["members_unreadable"] == 1

    def test_members_are_previewed_but_the_count_is_honest(self, contacts_design):
        contacts_design(sets=[_ContactSetRow("Big", members=[_MemberOcc(f"P{i}:1") for i in range(30)])])
        out = _payload(ap.handler(include=["contacts"]))
        row = out["contacts"][0]
        assert len(row["members"]) == 12              # the member preview cap
        assert row["member_count"] == 30              # the truth, uncapped
        assert row["members_truncated"] is True
        assert out["contacts_truncated"] is True and "members_truncated" in out["note"]

    def test_one_two_and_many_sets_are_all_listed(self, contacts_design):
        for n in (1, 2, 6):
            contacts_design(sets=[_ContactSetRow(f"CS{i}") for i in range(n)])
            out = _payload(ap.handler(include=["contacts"]))
            assert out["contact_count"] == n and len(out["contacts"]) == n

    def test_the_list_cap_truncates_and_flags_it(self, contacts_design):
        contacts_design(sets=[_ContactSetRow(f"CS{i}") for i in range(5)])
        out = _payload(ap.handler(include=["contacts"], max_contacts=2))
        assert len(out["contacts"]) == 2 and out["contact_count"] == 5
        assert out["contacts_truncated"] is True and "max_contacts" in out["note"]

    def test_analysis_on_with_the_sets_is_reported_without_an_inert_warning(self, contacts_design):
        contacts_design(sets=[_ContactSetRow("CS1")], enabled=True, use_sets=True)
        out = _payload(ap.handler(include=["contacts"]))
        assert out["contact_analysis"] == {"enabled": True, "scope": "contact_sets"}
        assert "INERT" not in out["note"]

    def test_analysis_on_but_scoped_to_all_bodies_says_the_sets_are_ignored(self, contacts_design):
        # analysis being ON is not enough: scoped to all bodies, every set listed takes no part, and
        # a list with no disclosure reads as "these are in force".
        contacts_design(sets=[_ContactSetRow("CS1")], enabled=True, use_sets=False)
        out = _payload(ap.handler(include=["contacts"]))
        assert out["contact_analysis"]["scope"] == "all_bodies"
        assert "IGNORED" in out["note"] and "set_analysis_scope" in out["note"]
        assert "INERT" not in out["note"]            # that word is the analysis-OFF case

    def test_an_unreadable_scope_is_null_not_all_bodies(self, contacts_design):
        # all_bodies would be a fabricated answer for a flag that never answered - and it would also
        # trigger the ignored-sets disclosure over a scope nobody read.
        contacts_design(sets=[_ContactSetRow("CS1")], enabled=True,
                        design_cls=_UnreadableScopeDesign)
        out = _payload(ap.handler(include=["contacts"]))
        assert out["contact_analysis"] == {"enabled": True, "scope": None}
        assert "IGNORED" not in out["note"]

    def test_an_unreadable_member_list_is_not_reported_as_an_empty_set(self, contacts_design):
        # member_count 0 would claim the set holds nothing; null plus the flag leaves it unclaimed.
        contacts_design(sets=[_UnreadableMembers("Opaque")])
        row = _payload(ap.handler(include=["contacts"]))["contacts"][0]
        assert row["member_count"] is None and row["members_unreadable"] is True
        assert "members" not in row and "members_truncated" not in row
        assert _payload(ap.handler(include=["contacts"]))["contacts_truncated"] is False

    def test_sets_listed_while_analysis_is_off_are_flagged_inert(self, contacts_design):
        # the list alone would read as "these are in force"; with analysis off none of them acts.
        contacts_design(sets=[_ContactSetRow("CS1")], enabled=False)
        out = _payload(ap.handler(include=["contacts"]))
        assert "INERT" in out["note"] and "enable_analysis" in out["note"]

    def test_no_inert_warning_when_there_are_no_sets(self, contacts_design):
        contacts_design(enabled=False)
        assert "INERT" not in _payload(ap.handler(include=["contacts"]))["note"]

    def test_contacts_composes_with_the_other_slices(self, contacts_design):
        contacts_design(sets=[_ContactSetRow("CS1")])
        out = _payload(ap.handler(include=["contacts", "relations"]))
        assert out["contact_count"] == 1
        assert out["relation_counts"] == {"rigid_groups": 0, "motion_links": 0, "constraints": 0}


class TestSuppressedJointDisclosure:
    def test_suppressed_joint_counted_once_and_disclosed(self):
        # A suppressed joint is inert, not broken: healthy stays true, the record carries
        # is_suppressed, and the rollup names it in suppressed_joints (measured defects:
        # a plain-healthy read, and a dead token double-counting one joint).
        j = FakeJoint("Hinge", 1, "A:1", "B:1", health_state=3)
        j.isSuppressed = True
        _install([], [j])
        out = _payload(ap.handler())
        assert out["joint_count"] == 1
        assert out["suppressed_joints"] == ["Hinge"]
        rec = out["joints"][0]
        assert rec["is_suppressed"] is True and rec["healthy"] is True
        assert out["is_healthy"] is True

    def test_active_joint_carries_no_suppression_field(self):
        j = FakeJoint("Hinge", 1, "A:1", "B:1")
        j.isSuppressed = False
        _install([], [j])
        rec = _payload(ap.handler())["joints"][0]
        assert "is_suppressed" not in rec


class TestBrokenRelationInHeadline:
    def test_failed_constraint_drops_is_healthy_without_the_relations_slice(self):
        # Measured: a failed assembly constraint left is_healthy true and showed only under
        # include=['relations'] - the headline flag now folds relation health in.
        con = type("C", (), {"name": "Constraint 1", "healthState": 2,
                             "errorOrWarningMessage": "conflicts with a joint"})()
        _install([], [])
        ap.app.activeProduct.rootComponent.assemblyConstraints = _Coll([con])
        out = _payload(ap.handler())
        assert out["is_healthy"] is False
        assert out["broken_relations"] == [
            {"kind": "constraint", "name": "Constraint 1", "error": "conflicts with a joint"}]

    def test_healthy_relations_leave_the_headline_alone(self):
        con = type("C", (), {"name": "Constraint 1", "healthState": 0})()
        _install([], [])
        ap.app.activeProduct.rootComponent.assemblyConstraints = _Coll([con])
        out = _payload(ap.handler())
        assert out["is_healthy"] is True and out["broken_relations"] == []


@pytest.fixture
def kin_design(monkeypatch):
    """A design of occurrences + joints wired into assembly_get through a fixture, so the patches
    undo themselves. all_occs is what root.allOccurrences reports - the NESTED walk - while occs is
    the top-level root.occurrences."""
    def _build(occs=(), joints=(), all_occs=None, asbuilt=()):
        design = FakeDesign(list(occs), list(joints), asbuilt=asbuilt, all_occs=all_occs)
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ap, "app", fake_app)
        monkeypatch.setattr(ap._common, "app", fake_app)
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        return design
    return _build


# ── value_now: the joint's CURRENT driven value, straight off its motion ─────────────────────────
#
# The motion class carries the value (RevoluteJointMotion.rotationValue in radians,
# SliderJointMotion.slideValue in cm, PlanarJointMotion's rotation + both slides). Without it on the
# row a caller has to infer a joint angle from the occurrences' basis vectors. The wire units are the
# ones the row's LIMITS already use: degrees and mm.

class TestJointValueNow:
    def test_revolute_angle_is_converted_from_radians_to_degrees(self, kin_design):
        kin_design(joints=[FakeJoint("Hinge", 1, "A:1", "B:1",
                                     motion_values={"rotationValue": math.radians(30)})])
        rec = _payload(ap.handler())["joints"][0]
        assert rec["value_now"] == {"angle_deg": 30.0}

    def test_slider_value_is_converted_from_cm_to_mm(self, kin_design):
        kin_design(joints=[FakeJoint("Travel", 2, "A:1", "B:1",
                                     motion_values={"slideValue": 2.5})])
        rec = _payload(ap.handler())["joints"][0]
        assert rec["value_now"] == {"slide_mm": 25.0}

    def test_planar_reports_its_rotation_and_both_slides(self, kin_design):
        kin_design(joints=[FakeJoint("Pad", 5, "A:1", "B:1",
                                     motion_values={"rotationValue": math.radians(-45),
                                                    "primarySlideValue": 1.2,
                                                    "secondarySlideValue": -0.35})])
        rec = _payload(ap.handler())["joints"][0]
        assert rec["value_now"] == {"angle_deg": -45.0, "slide_primary_mm": 12.0,
                                    "slide_secondary_mm": -3.5}

    def test_rigid_joint_carries_no_value(self, kin_design):
        # a rigid motion exposes no value at all - an invented 0.0 would read as "driven to zero".
        kin_design(joints=[FakeJoint("Locked", 0, "A:1", "B:1")])
        assert "value_now" not in _payload(ap.handler())["joints"][0]

    def test_an_unreadable_value_is_omitted_not_reported_as_zero(self, kin_design):
        kin_design(joints=[FakeJoint("Hinge", 1, "A:1", "B:1", motion_cls=_UnreadableMotion)])
        assert "value_now" not in _payload(ap.handler())["joints"][0]

    def test_value_now_is_the_current_pose_beside_the_limits(self, kin_design):
        # the two are separate reads in the same units: 30 deg driven inside a +/-60 deg limit.
        j = FakeJoint("Swing", 1, "A:1", "B:1", motion_values={"rotationValue": math.radians(30)})
        j.jointMotion.rotationLimits = SimpleNamespace(
            isMinimumValueEnabled=True, minimumValue=math.radians(-60),
            isMaximumValueEnabled=True, maximumValue=math.radians(60),
            isRestValueEnabled=False, restValue=None)
        kin_design(joints=[j])
        rec = _payload(ap.handler())["joints"][0]
        assert rec["value_now"] == {"angle_deg": 30.0}
        assert rec["rotation_limits_deg"] == {"min": -60.0, "max": 60.0}


# ── frame: the joint's own frame in WORLD coordinates ────────────────────────────────────────────
#
# geometryOrOriginOne/Two expose the frame world-framed: .origin plus primaryAxisVector (the frame
# Z), secondaryAxisVector (X) and thirdAxisVector (Y). The frame Z is the axis a joint OFFSET drives
# along, which is the fact a caller otherwise pays a probe cycle to discover.

class TestJointFrame:
    def test_frame_reports_the_world_origin_and_axes_of_the_first_geometry(self, kin_design):
        f1 = _JointFrame(origin=(6.3027, 0.0, 1.6), z=(1, 0, 0), x=(0, 0, -1), y=(0, 1, 0))
        kin_design(joints=[FakeJoint("Grip", 0, "A:1", "B:1", frame_one=f1)])
        frame = _payload(ap.handler(units="mm"))["joints"][0]["frame"]
        assert frame["origin"] == [63.027, 0.0, 16.0]     # 6.3027 cm -> 63.027 mm
        assert frame["z_axis"] == [1.0, 0.0, 0.0]         # primaryAxisVector
        assert frame["x_axis"] == [0.0, 0.0, -1.0]        # secondaryAxisVector
        assert frame["y_axis"] == [0.0, 1.0, 0.0]         # thirdAxisVector

    def test_frame_falls_back_to_the_second_geometry_when_the_first_is_null(self, kin_design):
        # an inferred joint reads null on geometryOrOriginOne; the frame is still readable off Two.
        f2 = _JointFrame(origin=(0.0, 4.0, 0.0), z=(0, 1, 0), x=(1, 0, 0), y=(0, 0, -1))
        kin_design(joints=[FakeJoint("Inferred", 1, "A:1", "B:1", frame_one=None, frame_two=f2)])
        frame = _payload(ap.handler(units="mm"))["joints"][0]["frame"]
        assert frame["origin"] == [0.0, 40.0, 0.0]
        assert frame["z_axis"] == [0.0, 1.0, 0.0]

    def test_the_first_geometry_wins_when_both_are_present(self, kin_design):
        one = _JointFrame(origin=(1.0, 0.0, 0.0), z=(1, 0, 0))
        two = _JointFrame(origin=(0.0, 9.0, 0.0), z=(0, 0, 1))
        kin_design(joints=[FakeJoint("Pin", 1, "A:1", "B:1", frame_one=one, frame_two=two)])
        frame = _payload(ap.handler(units="mm"))["joints"][0]["frame"]
        assert frame["origin"] == [10.0, 0.0, 0.0] and frame["z_axis"] == [1.0, 0.0, 0.0]

    def test_no_frame_key_when_both_geometries_are_null(self, kin_design):
        kin_design(joints=[FakeJoint("Bare", 1, "A:1", "B:1")])
        assert "frame" not in _payload(ap.handler())["joints"][0]

    def test_no_frame_key_when_the_geometry_answers_nothing(self, kin_design):
        # a frame of four nulls claims a frame that was never read.
        kin_design(joints=[FakeJoint("Opaque", 1, "A:1", "B:1", frame_one=_OpaqueFrame(),
                                     frame_two=_OpaqueFrame())])
        assert "frame" not in _payload(ap.handler())["joints"][0]

    def test_a_geometry_that_answers_nothing_does_not_shadow_the_second(self, kin_design):
        good = _JointFrame(origin=(0.0, 0.0, 5.0), z=(0, 0, 1))
        kin_design(joints=[FakeJoint("Pin", 1, "A:1", "B:1", frame_one=_OpaqueFrame(),
                                     frame_two=good)])
        frame = _payload(ap.handler(units="mm"))["joints"][0]["frame"]
        assert frame["origin"] == [0.0, 0.0, 50.0] and frame["z_axis"] == [0.0, 0.0, 1.0]

    def test_frame_origin_follows_the_units_but_the_axes_do_not(self, kin_design):
        # a direction is dimensionless - scaling it by the unit factor would corrupt it.
        f1 = _JointFrame(origin=(2.54, 0.0, 0.0), z=(0, 0, 1))
        kin_design(joints=[FakeJoint("Grip", 1, "A:1", "B:1", frame_one=f1)])
        frame = _payload(ap.handler(units="in"))["joints"][0]["frame"]
        assert frame["origin"] == [1.0, 0.0, 0.0]         # 2.54 cm -> 1 inch
        assert frame["z_axis"] == [0.0, 0.0, 1.0]

    def test_a_joint_origin_reference_reports_its_offset_world_position(self, monkeypatch,
                                                                        kin_design):
        # a JointOrigin exposes the three axis vectors but no origin of its own: its position is the
        # base anchor PLUS offsetX/Y/Z along the frame axes. A plain .origin read reports null here.
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion, "JointOrigin", _SliceJO, raising=False)
        jo = _SliceJO("Stock_Center", pos=(0.0, 0.0, 0.0), offsets=(0.0, 0.0, 4.5), token="T")
        kin_design(joints=[FakeJoint("Grip", 0, "A:1", "B:1", frame_one=jo)])
        frame = _payload(ap.handler(units="mm"))["joints"][0]["frame"]
        assert frame["origin"] == [0.0, 0.0, 45.0]      # base (0,0,0) + offsetZ 4.5 cm along +Z
        assert frame["z_axis"] == [0.0, 0.0, 1.0]

    def test_the_note_teaches_that_the_frame_z_is_the_offset_axis(self, kin_design):
        kin_design(joints=[FakeJoint("Grip", 1, "A:1", "B:1", frame_one=_JointFrame())])
        note = _payload(ap.handler())["note"]
        assert "z_axis is the direction a joint OFFSET drives along" in note
        assert "value_now" in note

    def test_no_joint_teaching_when_joints_are_not_emitted(self, kin_design):
        kin_design(occs=[FakeOcc("A:1", "A")],
                   joints=[FakeJoint("Grip", 1, "A:1", "B:1", frame_one=_JointFrame())])
        assert "OFFSET drives along" not in _payload(ap.handler(include_joints=False))["note"]


# ── include=['all_occurrences']: the NESTED occurrences, each with its full path ─────────────────
#
# The 'occurrences' array walks root.occurrences - top-level only - so a part inside a sub-assembly
# appears nowhere in it and its position is unreadable. This slice repeats the same record over
# root.allOccurrences, the only walk that reaches nested instances and reports true full paths.

class TestAllOccurrencesSlice:
    def test_default_omits_the_slice_and_advertises_it(self, kin_design):
        kin_design(occs=[FakeOcc("Tower:1", "Tower")])
        out = _payload(ap.handler())
        assert "all_occurrences" not in out and "all_occurrence_count" not in out
        assert "include=['all_occurrences']" in out["note"]

    def test_a_nested_occurrence_is_listed_with_its_full_path_and_position(self, kin_design):
        # the drift case: the child moved inside its parent, and the top-level rows cannot show it.
        top = FakeOcc("Tower:1", "Tower")
        nested = FakeOcc("Bolt:1", "Bolt", origin=(1.0, 2.0, 3.0),
                         full_path="Tower:1+Bolt:1")
        kin_design(occs=[top], all_occs=[top, nested])
        out = _payload(ap.handler(include=["all_occurrences"], units="mm"))
        rows = {r["full_path"]: r for r in out["all_occurrences"]}
        assert set(rows) == {"Tower:1", "Tower:1+Bolt:1"}
        assert rows["Tower:1+Bolt:1"]["origin"] == [10.0, 20.0, 30.0]
        assert rows["Tower:1+Bolt:1"]["component"] == "Bolt"
        # the top-level array and its count stay top-level - the slice ADDS a view, never edits one
        assert out["occurrence_count"] == 1 and len(out["occurrences"]) == 1

    def test_the_slice_row_is_the_same_record_as_a_top_level_row(self, kin_design):
        occ = FakeOcc("Tower:1", "Tower", origin=(1.0, 0.0, 0.0), body_bbox=((0, 0, 0), (2, 1, 1)),
                      grounded=True, ground_to_parent=True, body_count=3,
                      basis=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        kin_design(occs=[occ], joints=[FakeJoint("J1", 1, "Tower:1", None)])
        out = _payload(ap.handler(include=["all_occurrences"], units="mm"))
        top, nested = out["occurrences"][0], out["all_occurrences"][0]
        assert set(nested) - set(top) == {"full_path"}       # the ONE key the slice adds
        assert {k: v for k, v in nested.items() if k != "full_path"} == top
        assert nested["grounded"] is True and nested["ground_to_parent"] is True
        assert nested["body_count"] == 3 and nested["joints"] == ["J1"]
        assert nested["bbox_size"] == [20.0, 10.0, 10.0] and nested["z_axis"] == [0.0, 0.0, 1.0]

    def test_the_slice_walks_all_occurrences_not_the_top_level_collection(self, kin_design):
        # root.occurrences holds ONE occurrence while the design holds three - a slice built on the
        # top-level collection would report the two nested ones as not existing.
        top = FakeOcc("Tower:1", "Tower")
        kids = [FakeOcc(f"Bolt:{i}", "Bolt", full_path=f"Tower:1+Bolt:{i}") for i in (1, 2)]
        kin_design(occs=[top], all_occs=[top] + kids)
        out = _payload(ap.handler(include=["all_occurrences"]))
        assert out["all_occurrence_count"] == 3 and len(out["all_occurrences"]) == 3
        assert out["all_occurrences_truncated"] is False

    def test_two_nested_instances_sharing_a_leaf_name_are_told_apart_by_path(self, kin_design):
        # 'Bolt:1' under two different parents is the same leaf name twice; only the path separates
        # them, so a name-keyed row would collapse the pair.
        a = FakeOcc("Bolt:1", "Bolt", full_path="Left:1+Bolt:1")
        b = FakeOcc("Bolt:1", "Bolt", full_path="Right:1+Bolt:1")
        kin_design(occs=[], all_occs=[a, b])
        rows = _payload(ap.handler(include=["all_occurrences"]))["all_occurrences"]
        assert sorted(r["full_path"] for r in rows) == ["Left:1+Bolt:1", "Right:1+Bolt:1"]

    def test_the_cap_truncates_the_list_and_flags_it(self, kin_design):
        kids = [FakeOcc(f"P{i}:1", "P", full_path=f"Tower:1+P{i}:1") for i in range(9)]
        kin_design(occs=[], all_occs=kids)
        out = _payload(ap.handler(include=["all_occurrences"], max_all_occurrences=4))
        assert len(out["all_occurrences"]) == 4
        assert out["all_occurrence_count"] == 9          # the truth, uncapped
        assert out["all_occurrences_truncated"] is True
        assert "max_all_occurrences" in out["note"]

    def test_an_uncapped_list_is_not_flagged(self, kin_design):
        kin_design(occs=[], all_occs=[FakeOcc("A:1", "A")])
        out = _payload(ap.handler(include=["all_occurrences"]))
        assert out["all_occurrences_truncated"] is False and "max_all_occurrences" not in out["note"]

    def test_an_empty_design_reports_an_empty_list(self, kin_design):
        kin_design()
        out = _payload(ap.handler(include=["all_occurrences"]))
        assert out["all_occurrences"] == [] and out["all_occurrence_count"] == 0

    def test_include_joints_false_drops_the_joints_key_from_the_slice_rows(self, kin_design):
        kin_design(occs=[FakeOcc("A:1", "A")], joints=[FakeJoint("J", 1, "A:1", None)])
        out = _payload(ap.handler(include=["all_occurrences"], include_joints=False))
        assert "joints" not in out["all_occurrences"][0]


class TestJointLimitsRead:
    def test_enabled_limits_are_published_in_the_record(self):
        # Limits were WRITE-ONLY on this surface (measured) - the record now reads them back.
        import math, types as _t
        j = FakeJoint("Swing", 1, "A:1", "B:1")
        j.jointMotion.rotationLimits = _t.SimpleNamespace(
            isMinimumValueEnabled=True, minimumValue=math.radians(-60),
            isMaximumValueEnabled=True, maximumValue=math.radians(60),
            isRestValueEnabled=False, restValue=None)
        _install([], [j])
        rec = _payload(ap.handler())["joints"][0]
        assert rec["rotation_limits_deg"] == {"min": -60.0, "max": 60.0}
        assert "slide_limits_mm" not in rec

    def test_disabled_limits_stay_off_the_wire(self):
        j = FakeJoint("Swing", 1, "A:1", "B:1")
        _install([], [j])
        rec = _payload(ap.handler())["joints"][0]
        assert "rotation_limits_deg" not in rec and "slide_limits_mm" not in rec
