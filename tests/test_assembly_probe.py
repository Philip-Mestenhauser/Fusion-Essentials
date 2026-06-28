"""Unit tests for ``assembly_probe.py`` — structured kinematic state of an assembly.

This is the read tool that lets the agent reason about grounding/position/joint-wiring from NUMBERS
instead of a cluttered screenshot. Pinned: units scaling on positions, the joint-type -> friendly +
DOF mapping, per-occurrence ground flags + bbox, joint connection records, and the
occurrence<->joint cross-index.
"""

import json

from conftest import load_tool

ap = load_tool("assembly_probe")


class _Pt:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _BBox:
    def __init__(self, mn, mx):
        self.minPoint = _Pt(*mn); self.maxPoint = _Pt(*mx)


class _Trans:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class FakeOcc:
    def __init__(self, name, comp, origin=(0, 0, 0), bbox=None,
                 grounded=False, ground_to_parent=False, body_count=1):
        self.name = name
        self.component = type("C", (), {"name": comp})()
        self.transform2 = type("T", (), {"translation": _Trans(*origin)})()
        self.boundingBox = _BBox(*bbox) if bbox else None
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


class FakeRoot:
    def __init__(self, occs, joints):
        self.occurrences = _Coll(occs)
        self.joints = _Coll(joints)


class FakeDesign:
    def __init__(self, occs, joints, timeline=None):
        self.rootComponent = FakeRoot(occs, joints)
        self.timeline = _Coll(timeline or [])


def _install(occs, joints, timeline=None):
    design = FakeDesign(occs, joints, timeline)
    ap.app = type("A", (), {"activeProduct": design})()
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


class TestProbe:
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
