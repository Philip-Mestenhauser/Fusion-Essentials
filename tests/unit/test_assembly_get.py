"""Unit tests for ``assembly_get.py`` — structured kinematic state of an assembly.

This is the read tool that lets the agent reason about grounding/position/joint-wiring from NUMBERS
instead of a cluttered screenshot. Pinned: units scaling on positions, the joint-type -> friendly +
DOF mapping, per-occurrence ground flags + bbox, joint connection records, and the
occurrence<->joint cross-index.
"""

import json
from types import SimpleNamespace

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
                 grounded=False, ground_to_parent=False, body_count=1, basis=None):
        self.name = name
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


class FakeJoint:
    def __init__(self, name, motion_type, occ1, occ2, health_state=0, message=""):
        self.name = name
        self.jointMotion = type("M", (), {"jointType": motion_type})()
        self.occurrenceOne = type("O", (), {"name": occ1})() if occ1 else None
        self.occurrenceTwo = type("O", (), {"name": occ2})() if occ2 else None
        self.healthState = health_state          # 0 = healthy, non-zero = error/warning
        self.errorOrWarningMessage = message


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
    def __init__(self, occs, joints, root_bodies=(), asbuilt=()):
        self.occurrences = _Coll(occs)
        self.joints = _Coll(joints)
        self.asBuiltJoints = _Coll(asbuilt)
        self.bRepBodies = _Coll([_NamedBody(n) for n in root_bodies])


class FakeDesign:
    def __init__(self, occs, joints, timeline=None, root_bodies=(), asbuilt=(), marker=None):
        self.rootComponent = FakeRoot(occs, joints, root_bodies, asbuilt)
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
    NOT report is_healthy over an incomplete model. (The state a non-restoring joint_edit once left.)"""

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


# ── HEALTH: the thing a user sees FIRST (Compute Failed), which the probe was blind to ──────────
#
# A joint can be created + wired correctly yet FAIL TO COMPUTE (mis-axised -> over-constrained).
# The probe must surface that (is_healthy / broken_joints / per-joint healthy + timeline_problems)
# so it never reports a broken assembly as fine. Caught live: PistonSlide1 healthState=1 while the
# probe said everything was structurally great.

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
