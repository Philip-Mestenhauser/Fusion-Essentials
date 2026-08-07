"""Unit tests for ``assembly_joints_advanced.py`` - assembly_capture_position, joint_create_as_built, assembly_constrain.

The nuances pinned, no live Fusion:

  assembly_capture_position — the timeline pose mechanic. Capture is only valid when a move
  is pending (Design.snapshots.hasPendingSnapshot); 'revert' deletes the latest
  snapshot back to the joint-defined state; 'status' reports pending + count.

  joint_create_as_built — a joint where parts ALREADY are; createInput(occ1, occ2, None)
  for a rigid as-built, and createInput with a real JointGeometry for every other motion
  type (Fusion refuses a non-rigid as-built joint whose geometry is null). The input's
  motion setters take the JointInput arity; the created joint's motion is read back.

  assembly_constrain — the new Constrain Components: build geometric relationships
  between two occurrences' entities (type inferred: flush/coincident/concentric/
  angle). Here we pin the occurrence resolution + input assembly, with geometry
  entities supplied as opaque tokens (real BRep proxies need a live session).
"""

import json

import pytest

from conftest import load_tool

ja = load_tool("assembly_joints_advanced")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeSnapshot:
    def __init__(self, name="Snapshot1", timeline_index=0, delete_ok=True, survives_delete=False):
        self.name = name
        self.deleted = False
        self.timelineObject = type("TL", (), {"index": timeline_index})()
        self._delete_ok = delete_ok
        self._survives_delete = survives_delete   # simulate deleteMe()==True but no actual removal
        self._parent = None

    def deleteMe(self):
        if not self._delete_ok:
            return False
        self.deleted = True
        if self._parent is not None and not self._survives_delete:
            self._parent._remove(self)
        return True


class FakeSnapshots:
    def __init__(self, pending=False, items=(), revert_pending_ok=True, revert_pending_lies=False,
                 blind_after_revert=False):
        self._pending = pending
        self._items = list(items)
        for it in self._items:
            it._parent = self
        self.added = False
        self.reverted_pending = False
        self._revert_pending_ok = revert_pending_ok
        self._revert_pending_lies = revert_pending_lies   # returns True, flag stays set
        self._blind_after_revert = blind_after_revert     # the flag read RAISES after the revert
        self._blind = False

    @property
    def hasPendingSnapshot(self):
        if self._blind:
            raise RuntimeError("pending flag unreadable")
        return self._pending

    @hasPendingSnapshot.setter
    def hasPendingSnapshot(self, value):
        self._pending = value

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def add(self):
        self.added = True
        snap = FakeSnapshot(f"Snapshot{len(self._items) + 1}")
        snap._parent = self
        self._items.append(snap)
        self.hasPendingSnapshot = False
        return snap

    def revertPendingSnapshot(self):
        """Clears hasPendingSnapshot and returns whether that took; the captured snapshots stay put.
        revert_pending_lies models a True return with the flag still set, blind_after_revert a flag
        that cannot be read afterwards. Live, the discarded pose falls back to the last captured
        position, or to the joint rest pose when nothing was ever captured."""
        self.reverted_pending = True
        if not self._revert_pending_ok:
            return False
        if not self._revert_pending_lies:
            self._pending = False
        if self._blind_after_revert:
            self._blind = True
        return True

    def _remove(self, snap):
        if snap in self._items:
            self._items.remove(snap)


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name


class FakeAsBuiltInput:
    """An AsBuiltJointInput: its motion setters take the JointInput arity - the axis enum alone, with
    NO JointGeometry argument (an AsBuiltJointInput is not an AsBuiltJoint). A call carrying the extra
    geometry argument raises here, the way the overload does live."""

    def __init__(self):
        self.motion_calls = []
        self.jointMotion = None
        # a geometry IS readable off the input, so a wrong-arity call would have one to pass and
        # would fail for the arity, not for a missing attribute
        self.geometry = "GEOM_ON_INPUT"

    def _set(self, motion_class, args, arity):
        if len(args) != arity:
            raise TypeError(f"wrong number or type of arguments for {motion_class}")
        self.motion_calls.append((motion_class, args))
        self.jointMotion = type(motion_class, (), {})()
        return True

    def setAsRigidJointMotion(self, *a):
        return self._set("RigidJointMotion", a, 0)

    def setAsRevoluteJointMotion(self, *a):
        return self._set("RevoluteJointMotion", a, 1)

    def setAsSliderJointMotion(self, *a):
        return self._set("SliderJointMotion", a, 1)

    def setAsCylindricalJointMotion(self, *a):
        return self._set("CylindricalJointMotion", a, 1)

    def setAsPlanarJointMotion(self, *a):
        return self._set("PlanarJointMotion", a, 1)

    def setAsBallJointMotion(self, *a):
        return self._set("BallJointMotion", a, 2)

    def setAsPinSlotJointMotion(self, *a):
        return self._set("PinSlotJointMotion", a, 2)


class FakeAsBuiltJoints:
    """asBuiltJoints: createInput(occ1, occ2, geometry) + add(input). The created joint reports the
    motion the input carries, unless motion_class forces another (the platform-lies case: '' models a
    joint whose motion cannot be read at all)."""

    def __init__(self, motion_class=None, geometry_readback="ANCHOR", add_returns=True):
        self.last = None
        self.last_input = None
        self.added = 0
        self._motion_class = motion_class
        self._geometry_readback = geometry_readback
        self._add_returns = add_returns

    def createInput(self, o1, o2, geometry):
        self.last = (o1, o2, geometry)
        self.last_input = FakeAsBuiltInput()
        return self.last_input

    def add(self, inp):
        self.added += 1
        if not self._add_returns:
            return None
        cls = self._motion_class
        if cls is None:
            cls = type(inp.jointMotion).__name__ if inp.jointMotion is not None else "RigidJointMotion"
        motion = type(cls, (), {})() if cls else None
        return type("J", (), {"name": "AsBuilt1", "jointMotion": motion,
                              "geometry": self._geometry_readback})()


class FakeGeoRels:
    def __init__(self):
        self.added = []

    @property
    def count(self):
        return len(self.added)

    def add(self, *args):
        self.added.append(args)
        return ("rel", len(self.added))


class FakeConstraintInput:
    def __init__(self):
        self.geometricRelationships = FakeGeoRels()


class FakeAssemblyConstraints:
    def __init__(self):
        self.last_input = None

    def createInput(self):
        self.last_input = FakeConstraintInput()
        return self.last_input

    def add(self, inp):
        # the created constraint reflects however many relationships the input got
        n = inp.geometricRelationships.count
        return type("C", (), {"name": "Constraint1",
                              "geometricRelationships": type("R", (), {"count": n})()})()


class FakeRoot:
    def __init__(self, occurrences, abj, ac):
        self.allOccurrences = list(occurrences)
        self.asBuiltJoints = abj
        self.assemblyConstraints = ac


class FakeDesign:
    def __init__(self, occurrences, snapshots, abj, ac):
        self.rootComponent = FakeRoot(occurrences, abj, ac)
        self.snapshots = snapshots


def _install(occ_names, pending=False, snapshot_items=(), **snapshot_kwargs):
    snaps = FakeSnapshots(pending=pending, items=snapshot_items, **snapshot_kwargs)
    abj, ac = FakeAsBuiltJoints(), FakeAssemblyConstraints()
    occs = [FakeOcc(n) for n in occ_names]
    design = FakeDesign(occs, snaps, abj, ac)
    ja.app = type("A", (), {"activeProduct": design})()
    ja._common.app = ja.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design, snaps, abj, ac


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── assembly_capture_position ─────────────────────────────────────────────────────────

class TestCapturePosition:
    def test_capture_when_pending(self):
        _, snaps, _, _ = _install([], pending=True)
        out = _payload(ja.capture_position_handler(action="capture"))
        assert snaps.added is True
        assert out["captured"] is True

    def test_capture_with_nothing_pending_errors(self):
        _install([], pending=False)
        res = ja.capture_position_handler(action="capture")
        assert res["isError"] is True
        assert "no pending" in res["message"].lower()

    def test_phantom_capture_bites(self):
        # add() returns a snapshot object but the count never advances -> error, not ok
        _, snaps, _, _ = _install([], pending=True)
        snaps.add = lambda: FakeSnapshot("Phantom")
        res = ja.capture_position_handler(action="capture")
        assert res["isError"] is True
        assert "did not advance" in res["message"]

    def test_status_reports_pending_and_count(self):
        _install([], pending=True, snapshot_items=[FakeSnapshot()])
        out = _payload(ja.capture_position_handler(action="status"))
        assert out["has_pending"] is True
        assert out["snapshot_count"] == 1

    def test_status_lists_captured_markers(self):
        _install([], snapshot_items=[FakeSnapshot("Position1", timeline_index=3),
                                     FakeSnapshot("Position2", timeline_index=5)])
        out = _payload(ja.capture_position_handler(action="status"))
        assert out["markers"] == [{"name": "Position1", "timeline_index": 3},
                                  {"name": "Position2", "timeline_index": 5}]

    def test_delete_removes_named_marker(self):
        snap1, snap2 = FakeSnapshot("Position1"), FakeSnapshot("Position2")
        _, snaps, _, _ = _install([], snapshot_items=[snap1, snap2])
        out = _payload(ja.capture_position_handler(action="delete", marker="Position1"))
        assert out["deleted"] is True and out["marker"] == "Position1"
        assert snap1.deleted is True
        assert snaps.count == 1
        assert snaps.item(0).name == "Position2"

    def test_delete_is_case_insensitive(self):
        snap = FakeSnapshot("Position1")
        _install([], snapshot_items=[snap])
        out = _payload(ja.capture_position_handler(action="delete", marker="position1"))
        assert out["deleted"] is True

    def test_delete_declining_bool_errors(self):
        # Fusion declines the delete (deleteMe() returns False) -> error, not a false success.
        snap = FakeSnapshot("Position1", delete_ok=False)
        _install([], snapshot_items=[snap])
        res = ja.capture_position_handler(action="delete", marker="Position1")
        assert res["isError"] is True
        assert "declined" in res["message"].lower()
        assert snap.deleted is False

    def test_delete_unknown_name_lists_candidates(self):
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        res = ja.capture_position_handler(action="delete", marker="Ghost")
        assert res["isError"] is True
        assert "Position1" in res["message"] and "Position2" in res["message"]

    def test_delete_survivor_after_delete_is_error(self):
        # deleteMe() reports True but the snapshot is still in the collection on re-read -> error.
        snap = FakeSnapshot("Position1", survives_delete=True)
        _install([], snapshot_items=[snap])
        res = ja.capture_position_handler(action="delete", marker="Position1")
        assert res["isError"] is True
        assert "still present" in res["message"].lower()

    def test_delete_needs_marker_argument(self):
        _install([], snapshot_items=[FakeSnapshot("Position1")])
        res = ja.capture_position_handler(action="delete", marker="")
        assert res["isError"] is True and "marker" in res["message"].lower()

    def test_delete_with_no_snapshots_errors(self):
        _install([], snapshot_items=[])
        res = ja.capture_position_handler(action="delete", marker="Position1")
        assert res["isError"] is True and "nothing to delete" in res["message"].lower()

    def test_revert_deletes_latest_snapshot(self):
        snap = FakeSnapshot()
        _, snaps, _, _ = _install([], snapshot_items=[snap])
        out = _payload(ja.capture_position_handler(action="revert"))
        assert snap.deleted is True
        assert out["reverted"] is True

    def test_revert_with_no_snapshots_errors(self):
        _install([], snapshot_items=[])
        res = ja.capture_position_handler(action="revert")
        assert res["isError"] is True and "no captured" in res["message"].lower()

    def test_discard_pending_throws_the_uncaptured_move_away(self):
        snap = FakeSnapshot("Position1")
        _, snaps, _, _ = _install([], pending=True, snapshot_items=[snap])
        out = _payload(ja.capture_position_handler(action="discard_pending"))
        assert out["discarded"] is True
        assert out["has_pending"] is False
        assert snaps.hasPendingSnapshot is False
        # discarding the PENDING move is not reverting a CAPTURED one - the marker survives
        assert snap.deleted is False
        assert out["snapshot_count"] == 1

    def test_discard_pending_with_nothing_pending_errors_without_calling_the_api(self):
        _, snaps, _, _ = _install([], pending=False)
        res = ja.capture_position_handler(action="discard_pending")
        assert res["isError"] is True and "nothing to discard" in res["message"].lower()
        assert snaps.reverted_pending is False

    def test_discard_pending_declining_bool_errors(self):
        # revertPendingSnapshot() returns false -> the move still stands; never a false success.
        _install([], pending=True, revert_pending_ok=False)
        res = ja.capture_position_handler(action="discard_pending")
        assert res["isError"] is True and "declined" in res["message"].lower()

    def test_discard_pending_that_leaves_the_flag_set_is_an_error(self):
        # the platform returns True while the pending change survives - the re-read must catch it.
        _install([], pending=True, revert_pending_lies=True)
        res = ja.capture_position_handler(action="discard_pending")
        assert res["isError"] is True and "still" in res["message"].lower()

    def test_discard_pending_with_an_unreadable_flag_afterwards_is_an_error(self):
        # the confirming re-read RAISES: publishing has_pending False here would report a
        # measurement the tool never took, so the unreadable flag is an error, not a success.
        _install([], pending=True, blind_after_revert=True)
        res = ja.capture_position_handler(action="discard_pending")
        assert res["isError"] is True
        assert "could not be re-read" in res["message"]
        assert "status" in res["message"]

    def test_unknown_action(self):
        _install([])
        res = ja.capture_position_handler(action="frobnicate")
        assert res["isError"] is True and "must be one of" in res["message"]


# ── joint_create_as_built ───────────────────────────────────────────────────────────

@pytest.fixture
def as_built(monkeypatch):
    """Factory: install a design with two occurrences plus a configurable asBuiltJoints collection,
    and stub the shared '<occ>:<snap>'/handle resolver so 'geometry' yields an opaque JointGeometry
    (a real one needs a live session). Returns the asBuiltJoints fake."""
    def _make(occ_specs=(("A:1", "A:1"), ("B:1", "B:1")), **abj_kwargs):
        import adsk.fusion
        abj = FakeAsBuiltJoints(**abj_kwargs)
        design = FakeDesign([FakeOcc(n, full_path=fp) for n, fp in occ_specs],
                            FakeSnapshots(), abj, FakeAssemblyConstraints())
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ja, "app", fake_app)
        monkeypatch.setattr(ja._common, "app", fake_app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        # real classes, so is_as_built_joint / is_joint_origin are genuine isinstance checks rather
        # than the degrade-to-False path a Mock type takes
        monkeypatch.setattr(adsk.fusion, "AsBuiltJoint", type("AsBuiltJoint", (), {}))
        monkeypatch.setattr(adsk.fusion, "JointOrigin", type("JointOrigin", (), {}))
        monkeypatch.setattr(ja, "_resolve_input",
                            lambda d, spec: (f"JG[{spec}]", f"snap:{spec}", None))
        return abj
    return _make


class TestAsBuiltJoint:
    def test_rigid_as_built_passes_null_geometry(self, as_built):
        abj = as_built()
        out = _payload(ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="B:1"))
        o1, o2, geom = abj.last
        assert o1.name == "A:1" and o2.name == "B:1"
        assert geom is None                      # rigid as-built = null geometry
        assert out["created"] is True and out["joint_type"] == "rigid"
        # rigid sets no motion at all: null geometry IS the rigid contract
        assert abj.last_input.motion_calls == []

    def test_missing_occurrence_errors(self, as_built):
        as_built(occ_specs=(("A:1", "A:1"),))
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_requires_two_distinct(self, as_built):
        as_built(occ_specs=(("A:1", "A:1"),))
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="A:1")
        assert res["isError"] is True and "two distinct" in res["message"].lower()

    def test_same_local_name_different_path_is_allowed(self, as_built):
        # Two DISTINCT instances of the same component share a local .name ("Bolt:1") but
        # differ by fullPathName. The distinctness check must compare fullPathName, not .name - else it
        # false-positives and rejects a legitimate pair. Address each by its unambiguous fullPathName.
        abj = as_built(occ_specs=(("Bolt:1", "SubA/Bolt:1"), ("Bolt:1", "SubB/Bolt:1")))
        out = _payload(ja.as_built_joint_handler(
            occurrence_one="SubA/Bolt:1", occurrence_two="SubB/Bolt:1"))
        assert out["created"] is True
        o1, o2, _ = abj.last
        assert o1.fullPathName == "SubA/Bolt:1" and o2.fullPathName == "SubB/Bolt:1"

    def test_payload_names_each_occurrence_by_full_path(self, as_built):
        # A NESTED child's .name is only the leaf ("Inner:1") - the caller addressed it as
        # "Outer:1+Inner:1", and only the full path names it unambiguously, so that is what the
        # payload publishes.
        as_built(occ_specs=(("Base:1", "Base:1"), ("Inner:1", "Outer:1+Inner:1")))
        out = _payload(ja.as_built_joint_handler(
            occurrence_one="Base:1", occurrence_two="Outer:1+Inner:1"))
        assert out["occurrence_one"] == "Base:1"
        assert out["occurrence_two"] == "Outer:1+Inner:1"


class TestAsBuiltMotion:
    """A non-rigid as-built joint: Fusion REFUSES one whose createInput got a null geometry
    ("Geometry should not be null if joint motion is not rigid"), so the anchor is a precondition
    here, and the motion the joint comes back with is read off the created joint."""

    def _revolute(self, **kw):
        args = {"occurrence_one": "A:1", "occurrence_two": "B:1", "geometry": "A:1:top",
                "joint_type": "revolute"}
        args.update(kw)
        return ja.as_built_joint_handler(**args)

    def test_revolute_uses_the_one_arg_joint_input_setter(self, as_built):
        # The bite for the reuse claim: an AsBuiltJointInput takes the JointInput arity (the axis enum
        # ALONE). Routing it through the existing-as-built branch would call the two-arg
        # setter(axis, geometry), which raises in the fake - so this pins the dispatch, not just the type.
        abj = as_built()
        out = _payload(self._revolute(axis="x"))
        assert abj.last_input.motion_calls == [("RevoluteJointMotion", (0,))]
        assert out["joint_type"] == "revolute" and out["axis"] == "x"

    def test_resolved_geometry_reaches_create_input(self, as_built):
        abj = as_built()
        _payload(self._revolute())
        assert abj.last[2] == "JG[A:1:top]"      # createInput's third argument, not None
        assert _payload(self._revolute())["geometry"] == "snap:A:1:top"

    def test_non_rigid_without_geometry_is_refused_before_any_call(self, as_built):
        abj = as_built()
        res = self._revolute(geometry="")
        assert res["isError"] is True
        assert "geometry" in res["message"] and "revolute" in res["message"]
        assert abj.last is None and abj.added == 0

    def test_rigid_with_geometry_is_refused(self, as_built):
        # A rigid as-built joint anchors nowhere, so a supplied geometry would be silently dropped.
        abj = as_built()
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="B:1",
                                        geometry="A:1:top")
        assert res["isError"] is True
        assert "rigid" in res["message"] and "A:1:top" in res["message"]
        assert abj.added == 0

    def test_joint_origin_geometry_is_refused_naming_joint_create(self, as_built, monkeypatch):
        import adsk.fusion
        abj = as_built()
        monkeypatch.setattr(ja, "_resolve_input",
                            lambda d, spec: (adsk.fusion.JointOrigin(), "handle:joint_origin", None))
        res = self._revolute(geometry="H_JO")
        assert res["isError"] is True
        assert "Joint Origin" in res["message"] and "joint_create" in res["message"]
        assert abj.added == 0

    def test_unresolvable_geometry_error_is_surfaced(self, as_built, monkeypatch):
        abj = as_built()
        monkeypatch.setattr(ja, "_resolve_input",
                            lambda d, spec: (None, spec, "handle did not resolve - stale token"))
        res = self._revolute(geometry="H_DEAD")
        assert res["isError"] is True
        assert "geometry" in res["message"] and "stale token" in res["message"]
        assert abj.added == 0

    def test_motion_readback_mismatch_is_an_error(self, as_built):
        # the joint comes back RIGID when revolute was asked - a wrong result with a healthy feature
        as_built(motion_class="RigidJointMotion")
        res = self._revolute()
        assert res["isError"] is True
        assert "'rigid'" in res["message"] and "'revolute'" in res["message"]

    def test_unreadable_motion_is_an_error_for_a_motion_type(self, as_built):
        as_built(motion_class="")
        res = self._revolute()
        assert res["isError"] is True and "could not be read back" in res["message"]

    def test_rigid_survives_an_unreadable_motion_readback(self, as_built):
        # add() with a null geometry RAISES for any non-rigid motion, so reaching a created joint on
        # the rigid path is itself the proof - an unreadable motion class is not a failure there.
        as_built(motion_class="")
        out = _payload(ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["created"] is True and out["joint_type"] == "rigid"

    def test_a_rejected_motion_setter_stops_before_add(self, as_built, monkeypatch):
        abj = as_built()
        monkeypatch.setattr(ja, "_apply_motion",
                            lambda *a, **k: (False, "Invalid parameter pitchDirection"))
        res = self._revolute()
        assert res["isError"] is True and "pitchDirection" in res["message"]
        assert abj.added == 0

    def test_null_anchor_geometry_on_a_motion_joint_is_reported(self, as_built):
        # AsBuiltJoint.geometry reads null only for a rigid joint, so a revolute one with no
        # geometry to read is a signal - reported, not swallowed.
        as_built(geometry_readback=None)
        out = _payload(self._revolute())
        assert "anchor_warning" in out and "joint_drive" in out["anchor_warning"]

    def test_pin_slot_passes_two_distinct_directions(self, as_built):
        abj = as_built()
        out = _payload(self._revolute(joint_type="pin_slot", axis="z", slide_axis="x"))
        assert abj.last_input.motion_calls == [("PinSlotJointMotion", (2, 0))]
        assert out["slide_axis"] == "x"

    def test_pin_slot_slide_axis_must_differ_from_the_rotation_axis(self, as_built):
        abj = as_built()
        res = self._revolute(joint_type="pin_slot", axis="z", slide_axis="z")
        assert res["isError"] is True and "must differ" in res["message"]
        assert abj.added == 0

    def test_unknown_joint_type_is_refused(self, as_built):
        as_built()
        res = self._revolute(joint_type="hinge")
        assert res["isError"] is True and "hinge" in res["message"]

    def test_unknown_axis_is_refused(self, as_built):
        as_built()
        res = self._revolute(axis="w")
        assert res["isError"] is True and "Unknown axis 'w'" in res["message"]

    def test_add_returning_nothing_is_an_error(self, as_built):
        as_built(add_returns=False)
        res = self._revolute()
        assert res["isError"] is True and "returned nothing" in res["message"]


class TestAsBuiltResultNote:
    """What the note TELLS the agent after a successful create must match what was actually set -
    the axis the setter used, and a next step that will not refuse the joint."""

    def _make(self, **kw):
        args = {"occurrence_one": "A:1", "occurrence_two": "B:1", "geometry": "A:1:top"}
        args.update(kw)
        return _payload(ja.as_built_joint_handler(**args))

    def test_ball_note_claims_no_frame_axis(self, as_built):
        # setAsBallJointMotion ignores 'axis' (pitch Z / yaw X is the only pair the API accepts), and
        # the payload publishes axis=None - so the note must not name the requested axis.
        abj = as_built()
        out = self._make(joint_type="ball", axis="y")
        assert abj.last_input.motion_calls == [("BallJointMotion", (2, 0))]   # Z pitch, X yaw
        assert out["axis"] is None
        assert "y axis" not in out["note"]
        assert "pitch Z / yaw X" in out["note"]

    def test_axis_using_types_state_the_dof_the_setter_gave_that_axis(self, as_built):
        as_built()
        assert "rotating about the frame z axis" in self._make(joint_type="revolute")["note"]
        as_built()
        assert "sliding along the frame x axis" in self._make(joint_type="slider", axis="x")["note"]
        as_built()
        # planar's axis is the plane NORMAL - the motion is in the plane, not along the axis
        note = self._make(joint_type="planar", axis="z")["note"]
        assert "sliding in the plane normal to the frame z axis" in note

    def test_pin_slot_note_names_both_directions(self, as_built):
        as_built()
        note = self._make(joint_type="pin_slot", axis="z", slide_axis="x")["note"]
        assert "rotating about the frame z axis" in note
        assert "sliding along the frame x axis" in note

    def test_joint_drive_is_offered_only_for_the_types_it_drives(self, as_built):
        # joint_drive REFUSES ball/planar/pin_slot ("only revolute, slider, and cylindrical joints
        # can be driven by value"), so pointing those at it would hand the agent a dead end.
        for jt in ("revolute", "slider", "cylindrical"):
            as_built()
            assert "Pose it with joint_drive." in self._make(joint_type=jt)["note"]
        for jt in ("planar", "ball", "pin_slot"):
            as_built()
            note = self._make(joint_type=jt)["note"]
            assert "assembly_move" in note, jt
            assert "Pose it with joint_drive." not in note, jt

    def test_anchor_warning_points_at_the_right_tool_for_a_ball_joint(self, as_built):
        as_built(geometry_readback=None)
        warn = self._make(joint_type="ball")["anchor_warning"]
        assert "assembly_move" in warn and "Pose it with joint_drive." not in warn

    def test_rigid_note_is_unchanged(self, as_built):
        as_built()
        out = _payload(ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["note"] == "Occurrences rigidly joined where they already are."


# ── assembly_constrain ──────────────────────────────────────────────────────

class TestAssemblyConstraint:
    def test_missing_occurrence_errors(self):
        _install(["A:1"])
        res = ja.assembly_constraint_handler(occurrence_one="Ghost", occurrence_two="A:1")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_resolves_both_occurrences(self):
        # With no snaps and no selection, the handler should ask for geometry, not crash.
        _install(["A:1", "B:1"])
        res = ja.assembly_constraint_handler(occurrence_one="A:1", occurrence_two="B:1")
        assert res["isError"] is True
        assert "geometry" in res["message"].lower() or "select" in res["message"].lower()


class TestAssemblyConstraintSnaps:
    """Autonomous geometry snaps (no human selection) — '<occurrence>:<snap>'."""

    def _install_with_snaps(self, monkeypatch):
        design, snaps, abj, ac = _install(["TrussMast:1", "Boom:1"])
        # Stub the shared resolver: return a fake entity per (occ, snap).
        def fake_resolve(design_arg, occ_name, snap):
            return (f"ENT[{occ_name}:{snap}]", "planar", None)
        monkeypatch.setattr(ja, "_resolve_snap_entity", fake_resolve)
        return design, ac

    def test_snap_specs_resolve_and_build_relationship(self, monkeypatch):
        design, ac = self._install_with_snaps(monkeypatch)
        out = _payload(ja.assembly_constraint_handler(
            snap_one="TrussMast:1:top", snap_two="Boom:1:bottom", offset=0))
        # a relationship was added with the two resolved entities
        rels = ac.last_input.geometricRelationships.added
        assert len(rels) == 1
        e1, e2 = rels[0][0], rels[0][1]
        assert e1 == "ENT[TrussMast:1:top]" and e2 == "ENT[Boom:1:bottom]"
        assert out["created"] is True

    def test_compute_failed_constraint_bites(self, monkeypatch):
        # the constraint is ADDED but reads healthState 2 (failed to solve) -> error, not ok
        design, ac = self._install_with_snaps(monkeypatch)
        ac.add = lambda inp: type("C", (), {
            "name": "Constraint1", "healthState": 2,
            "errorOrWarningMessage": "over-constrained",
            "geometricRelationships": type("R", (), {"count": 1})()})()
        res = ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:bottom")
        assert res["isError"] is True
        assert "FAILED to solve" in res["message"]
        assert "over-constrained" in res["message"]

    def test_flip_defaults_false(self, monkeypatch):
        design, ac = self._install_with_snaps(monkeypatch)
        ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top",
                                       offset=10, units="mm")
        # flip is the 3rd arg of add(); unset it must be False (the offset VALUE
        # encoding is pinned in TestConstraintValueEncoding)
        args = ac.last_input.geometricRelationships.added[0]
        assert args[2] is False           # flipped

    def test_unresolvable_snap_errors(self, monkeypatch):
        _install(["A:1"])
        def fail_resolve(d, occ, snap):
            return (None, None, f"no '{snap}' on '{occ}'")
        monkeypatch.setattr(ja, "_resolve_snap_entity", fail_resolve)
        res = ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="A:1:bottom")
        assert res["isError"] is True
        assert "no 'top'" in res["message"] or "no 'bottom'" in res["message"]


class TestMultiRelationshipConstraint:
    """ONE constraint with MULTIPLE relationships solved together (Fusion's actual model) - avoids the
    over-determined skew a single-relationship-at-a-time constraint would produce."""

    def _stub(self, monkeypatch, design):
        def fake_resolve(d, occ, snap):
            return (f"ENT[{occ}:{snap}]", "planar", None)
        monkeypatch.setattr(ja, "_resolve_snap_entity", fake_resolve)

    def test_relationships_list_builds_one_constraint_many_rels(self, monkeypatch):
        design, snaps, abj, ac = _install(["Boom:1", "TrussMast:1"])
        self._stub(monkeypatch, design)
        out = _payload(ja.assembly_constraint_handler(relationships=[
            {"snap_one": "Boom:1:bottom", "snap_two": "TrussMast:1:top", "flip": True},
            {"snap_one": "Boom:1:back",   "snap_two": "TrussMast:1:back", "offset": 10},
            {"snap_one": "Boom:1:left",   "snap_two": "TrussMast:1:left", "offset": 30},
        ]))
        # ONE constraint, THREE relationships added to it
        added = ac.last_input.geometricRelationships.added
        assert len(added) == 3
        assert out["created"] is True
        assert out["relationship_count"] == 3

    def test_per_relationship_flip_respected(self, monkeypatch):
        design, snaps, abj, ac = _install(["A:1", "B:1"])
        self._stub(monkeypatch, design)
        ja.assembly_constraint_handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top", "flip": True},
            {"snap_one": "A:1:left",   "snap_two": "B:1:left"},   # flip defaults false
        ])
        added = ac.last_input.geometricRelationships.added
        assert added[0][2] is True     # flipped on first
        assert added[1][2] is False    # not on second

    def test_single_pair_still_works(self, monkeypatch):
        # back-compat: snap_one/snap_two shorthand == a one-relationship list
        design, snaps, abj, ac = _install(["A:1", "B:1"])
        self._stub(monkeypatch, design)
        out = _payload(ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top"))
        assert out["relationship_count"] == 1

    def test_bad_relationship_item_errors(self, monkeypatch):
        design, snaps, abj, ac = _install(["A:1"])
        self._stub(monkeypatch, design)
        res = ja.assembly_constraint_handler(relationships=[{"snap_one": "A:1:top"}])  # missing snap_two
        assert res["isError"] is True
        assert "snap_two" in res["message"]

    def test_relationships_must_be_a_list(self, monkeypatch):
        # passing a non-list (e.g. a dict or string) must error cleanly, not iterate chars/keys.
        design, snaps, abj, ac = _install(["A:1"])
        self._stub(monkeypatch, design)
        res = ja.assembly_constraint_handler(relationships={"snap_one": "A:1:top", "snap_two": "A:1:bottom"})
        assert res["isError"] is True
        assert "must be a list" in res["message"]


# ── the constraint VALUE encoding: offset (length, cm-scaled) vs angle (deg string) ─────────────
# rels.add(e1, e2, flip, value). 'value' is a ValueInput: an offset is createByReal(offset_cm) where
# offset_cm = offset * UNIT_TO_CM; an angle uses createByString("<deg> deg"). This is unit-conversion +
# a branch that the existing tests don't pin (they only check the flip arg).

class TestConstraintValueEncoding:
    def _stub(self, monkeypatch):
        design, snaps, abj, ac = _install(["A:1", "B:1"])
        monkeypatch.setattr(ja, "_resolve_snap_entity",
                            lambda d, occ, snap: (f"ENT[{occ}:{snap}]", "planar", None))
        import adsk.core
        # echo the encoded value so the test can assert which factory + magnitude was used
        adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
        adsk.core.ValueInput.createByString = staticmethod(lambda s: ("string", s))
        return ac

    def test_offset_scaled_to_cm(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top", offset=10, units="mm")
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("real", 1.0)        # 10 mm -> 1.0 cm via createByReal

    def test_offset_inch_scaling(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top", offset=2, units="in")
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value[0] == "real" and abs(value[1] - 5.08) < 1e-9   # 2 in -> 5.08 cm

    def test_unknown_units_errors_not_silently_treated_as_mm(self, monkeypatch):
        # An unrecognized unit must be REFUSED, not silently treated as mm, like joint_create errors
        # on the same bad input.
        self._stub(monkeypatch)
        res = ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top",
                                             offset=10, units="furlong")
        assert res["isError"] is True
        assert "furlong" in res["message"]

    def test_angle_uses_deg_string_not_offset(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.assembly_constraint_handler(relationships=[
            {"snap_one": "A:1:right", "snap_two": "B:1:left", "angle_deg": 30}])
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("string", "30.0 deg")  # angle path -> createByString, NOT a cm offset

    def test_zero_offset_is_real_zero(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top", offset=0)
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("real", 0.0)
