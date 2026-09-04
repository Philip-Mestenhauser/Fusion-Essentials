"""Unit tests for ``assembly_capture_position.py`` - the timeline POSITION marker verbs.

Fusion keeps geometry history separate from assembly positions, so a moved jointed component's
pose is TRANSIENT until captured. Pinned here, no live Fusion: the pending flag as the
precondition each verb gates on (tri-state - an unread flag is not a 'no'), the phantom capture
(the add reports success while the parts snap back), and every count read back off the collection
rather than computed.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

ja = load_tool("assembly_capture_position")
acom = load_tool("_assembly_common")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeSnapshot:
    def __init__(self, name="Snapshot1", timeline_index=0, delete_ok=True, survives_delete=False,
                 spawns_on_delete=False, deletes_instead=None):
        self.name = name
        self.deleted = False
        self.timelineObject = type("TL", (), {"index": timeline_index})()
        self._delete_ok = delete_ok
        self._survives_delete = survives_delete   # simulate deleteMe()==True but no actual removal
        # deleteMe()==True and the collection ends up LARGER: the count moves the other way, which
        # is the only reading that tells the two numbers in the refusal apart
        self._spawns_on_delete = spawns_on_delete
        # deleteMe()==True and the collection gives up a DIFFERENT marker: the count drops exactly
        # as a working removal's does while THIS one still stands, the shape no count comparison sees
        self._deletes_instead = deletes_instead
        self._parent = None

    def deleteMe(self):
        if not self._delete_ok:
            return False
        self.deleted = True
        if self._parent is not None:
            if self._spawns_on_delete:
                self._parent._add(FakeSnapshot(f"{self.name}_extra"))
            elif self._deletes_instead is not None:
                self._parent._remove(self._deletes_instead)
            elif not self._survives_delete:
                self._parent._remove(self)
            # armed whether or not the collection gave the marker up - a lying delete can leave a
            # survivor AND an unreadable count, a pairing _remove never reaches
            self._parent._delete_attempted()
        return True


class FakeSnapshots:
    def __init__(self, pending=False, items=(), revert_pending_ok=True, revert_pending_lies=False,
                 blind_after_revert=False, blind_count_after_delete=False,
                 blind_count_after_add=False, blind_count_after_discard=False,
                 blind_count_after_any_delete=False):
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
        # the COUNT read RAISES once the collection has been mutated - per mutation path, so a test
        # can blind exactly the re-read the handler takes after add / revertPendingSnapshot / delete
        self._blind_count_after_delete = blind_count_after_delete
        self._blind_count_after_add = blind_count_after_add
        self._blind_count_after_discard = blind_count_after_discard
        # blind_count_after_delete arms from _remove, which a marker that SURVIVES its own deleteMe
        # never reaches; this one arms from the deleteMe itself, so a survivor can arrive with a
        # count that will not re-read
        self._blind_count_after_any_delete = blind_count_after_any_delete
        self._blind_count = False

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
        if self._blind_count:
            raise RuntimeError("snapshot count unreadable")
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def add(self):
        self.added = True
        snap = FakeSnapshot(f"Snapshot{len(self._items) + 1}")
        snap._parent = self
        self._items.append(snap)
        self.hasPendingSnapshot = False
        if self._blind_count_after_add:
            self._blind_count = True
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
        if self._blind_count_after_discard:
            self._blind_count = True
        return True

    def _remove(self, snap):
        if snap in self._items:
            self._items.remove(snap)
        if self._blind_count_after_delete:
            self._blind_count = True

    def _add(self, snap):
        snap._parent = self
        self._items.append(snap)

    def _delete_attempted(self):
        """Called by every deleteMe() that answered True, removal or not - where
        blind_count_after_any_delete blinds the count read the handler takes next."""
        if self._blind_count_after_any_delete:
            self._blind_count = True


class FakeOcc:
    """An occurrence. 'pos' is its WORLD translation in cm, read through transform2 the way
    _occ_origin reads it; pos=None models an occurrence whose transform cannot be read at all."""

    def __init__(self, name, full_path=None, pos=None):
        self.name = name
        self.fullPathName = full_path or name
        self.pos = pos
        # A real Occurrence always answers `component`; a read that RAISES is the
        # unresolved-external-reference signal the shared occurrence census filters on.
        self.component = SimpleNamespace(name=name.split(":")[0])

    def _matrix(self):
        if self.pos is None:
            raise RuntimeError("transform unreadable")
        x, y, z = self.pos
        return SimpleNamespace(translation=SimpleNamespace(x=x, y=y, z=z))

    @property
    def transform2(self):
        return self._matrix()

    @property
    def transform(self):
        return self._matrix()


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

    def __init__(self, motion_class=None, geometry_readback="ANCHOR", add_returns=True,
                 name_sticks=True):
        self.last = None
        self.last_input = None
        self.added = 0
        self._motion_class = motion_class
        self._geometry_readback = geometry_readback
        self._add_returns = add_returns
        # name_sticks=False models the SWIG accept-and-ignore: the assignment does not raise, the
        # joint keeps the name Fusion gave it, and only a read-back notices.
        self._name_sticks = name_sticks

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
        attrs = {"name": "AsBuilt1", "jointMotion": motion,
                 "geometry": self._geometry_readback}
        if not self._name_sticks:
            attrs["name"] = property(lambda self: "AsBuilt1", lambda self, value: None)
        return type("J", (), attrs)()


class FakeGeoRels:
    """geometricRelationships, on the constraint INPUT (where rels are added) and on the CREATED
    constraint (where the count is read back). 'fixed' pins the count to something other than what was
    added; 'blind' makes the count unreadable - the case where publishing the REQUEST as the count
    would invent a measurement."""

    def __init__(self, fixed=None, blind=False):
        self.added = []
        self._fixed = fixed
        self._blind = blind

    @property
    def count(self):
        if self._blind:
            raise RuntimeError("relationship count unreadable")
        return len(self.added) if self._fixed is None else self._fixed

    def add(self, *args):
        self.added.append(args)
        return ("rel", len(self.added))


class FakeConstraintInput:
    def __init__(self):
        self.geometricRelationships = FakeGeoRels()


def created_constraint(health=0, message="", count=0, blind_health=False, blind_count=False):
    """The AssemblyConstraint add() hands back. healthState 0 = healthy, 1 = warning, 2 = error (the
    pair assembly_get publishes as healthy:false); blind_health models a state that cannot be READ at
    all, which needs a raising property rather than an attribute."""
    def _health(self):
        if blind_health:
            raise RuntimeError("healthState unreadable")
        return health
    return type("C", (), {
        "name": "Constraint1", "errorOrWarningMessage": message,
        "geometricRelationships": FakeGeoRels(fixed=count, blind=blind_count),
        "healthState": property(_health)})()


class FakeAssemblyConstraints:
    """assemblyConstraints: createInput() + add(input). The created constraint carries the health the
    test asks for and the relationship count the input actually received; on_add runs the assembly
    recompute the add triggers - what moves a part or breaks an existing joint."""

    def __init__(self, health=0, message="", blind_health=False, blind_count=False, count=None,
                 on_add=None):
        self.last_input = None
        self.added = 0
        self._health = health
        self._message = message
        self._blind_health = blind_health
        self._blind_count = blind_count
        self._count = count
        self.on_add = on_add

    def createInput(self):
        self.last_input = FakeConstraintInput()
        return self.last_input

    def add(self, inp):
        self.added += 1
        n = inp.geometricRelationships.count if self._count is None else self._count
        if self.on_add is not None:
            self.on_add()
        return created_constraint(health=self._health, message=self._message, count=n,
                                  blind_health=self._blind_health, blind_count=self._blind_count)


class FakeRoot:
    def __init__(self, occurrences, abj, ac):
        self.allOccurrences = list(occurrences)
        self.asBuiltJoints = abj
        self.assemblyConstraints = ac


class FakeDesign:
    def __init__(self, occurrences, snapshots, abj, ac, timeline=None):
        self.rootComponent = FakeRoot(occurrences, abj, ac)
        self.snapshots = snapshots
        # None = a design whose timeline cannot be read (what _common.timeline_health sees as empty)
        self.timeline = timeline


def _install(occ_names, pending=False, snapshot_items=(), **snapshot_kwargs):
    snaps = FakeSnapshots(pending=pending, items=snapshot_items, **snapshot_kwargs)
    abj, ac = FakeAsBuiltJoints(), FakeAssemblyConstraints()
    occs = [FakeOcc(n) for n in occ_names]
    design = FakeDesign(occs, snaps, abj, ac)
    ja._common.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design, snaps, abj, ac


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def as_built(monkeypatch):
    """Factory: install a design with two occurrences plus a configurable asBuiltJoints collection,
    and stub the shared '<occ>:<snap>'/handle resolver so 'geometry' yields an opaque JointGeometry
    (a real one needs a live session). Returns the asBuiltJoints fake."""
    def _make(occ_specs=(("A:1", "A:1"), ("B:1", "B:1")), pending=False, **abj_kwargs):
        import adsk.fusion
        abj = FakeAsBuiltJoints(**abj_kwargs)
        design = FakeDesign([FakeOcc(n, full_path=fp) for n, fp in occ_specs],
                            FakeSnapshots(pending=pending), abj, FakeAssemblyConstraints())
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ja._common, "app", fake_app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        # real classes, so is_as_built_joint / is_joint_origin are genuine isinstance checks rather
        # than the degrade-to-False path a Mock type takes
        monkeypatch.setattr(adsk.fusion, "AsBuiltJoint", type("AsBuiltJoint", (), {}))
        monkeypatch.setattr(adsk.fusion, "JointOrigin", type("JointOrigin", (), {}))
        return abj
    return _make


# ── assembly_capture_position ─────────────────────────────────────────────────────────

class TestCapturePosition:
    def test_capture_when_pending(self):
        _, snaps, _, _ = _install([], pending=True)
        out = _payload(ja.handler(action="capture"))
        assert snaps.added is True
        assert out["captured"] is True

    def test_capture_with_an_unreadable_count_publishes_null_not_the_arithmetic(self):
        # the count re-read RAISES after add(): publishing "one more than before" would hand the
        # caller a number the tool computed, not one it read off the collection.
        _install([], pending=True, blind_count_after_add=True)
        out = _payload(ja.handler(action="capture"))
        assert out["captured"] is True
        assert out["snapshot_count"] is None
        assert "'snapshot_count' is null" in out["note"]

    def test_capture_publishes_the_count_it_read_back(self):
        _install([], pending=True, snapshot_items=[FakeSnapshot("Position1")])
        out = _payload(ja.handler(action="capture"))
        assert out["snapshot_count"] == 2
        assert "null" not in out["note"]

    def test_a_capture_that_REVERTS_the_pending_move_bites(self):
        # The cardinal sin this gate exists for: snapshots.add() answers with a snapshot object AND
        # an advanced count while the moved part snaps back to where it stood before the move, so the
        # marker records the PRE-move pose. Neither the object nor the count says anything about the
        # POSITION - only the parts' own transforms do.
        moved = FakeOcc("PartB:1", pos=(2.5, 0.0, 0.0))     # cm: the pending, moved pose
        _, snaps, _, _ = _install([], pending=True)
        ja._common.app.activeProduct.rootComponent.allOccurrences = [moved]
        real_add = snaps.add

        def reverting_add():
            snap = real_add()
            moved.pos = (0.0, 0.0, 0.0)                     # the platform throws the move away
            return snap

        snaps.add = reverting_add
        res = ja.handler(action="capture")
        assert res["isError"] is True
        assert "does NOT hold the pose you captured" in res["message"]
        assert "PartB:1" in res["message"]
        assert "25.0 mm" in res["message"]                  # the snap-back, in mm
        assert "action='delete'" in res["message"]          # the stale marker is named for removal

    def test_a_capture_that_HOLDS_the_pose_is_confirmed(self):
        # the same shape with the pose held: ok, and 'pose_held' says the transforms were re-read
        held = FakeOcc("PartB:1", pos=(2.5, 0.0, 0.0))
        _install([], pending=True)
        ja._common.app.activeProduct.rootComponent.allOccurrences = [held]
        out = _payload(ja.handler(action="capture"))
        assert out["captured"] is True
        assert out["pose_held"] is True

    def test_a_pose_that_cannot_be_read_is_null_not_a_confident_yes(self):
        # the occurrence exists but its transform will not read, so whether the marker holds the
        # pending pose is UNKNOWN - published as null with the reason, never as a confirmed hold.
        blind = FakeOcc("PartB:1", pos=None)
        _install([], pending=True)
        ja._common.app.activeProduct.rootComponent.allOccurrences = [blind]
        out = _payload(ja.handler(action="capture"))
        assert out["captured"] is True
        assert out["pose_held"] is None
        assert "'pose_held' is null" in out["note"]

    # The snap-back tolerance is an EXACT boundary: _MOVE_TOL_CM is joint-solver noise, so a shift OF
    # exactly that much is not a revert (strictly greater wins) while a hair more is.
    def _capture_after_shift(self, shift_cm):
        occ = FakeOcc("PartB:1", pos=(0.0, 0.0, 0.0))
        _, snaps, _, _ = _install([], pending=True)
        ja._common.app.activeProduct.rootComponent.allOccurrences = [occ]
        real_add = snaps.add

        def shifting_add():
            snap = real_add()
            occ.pos = (shift_cm, 0.0, 0.0)
            return snap

        snaps.add = shifting_add
        return ja.handler(action="capture")

    def test_a_snap_back_of_exactly_the_tolerance_is_not_a_revert(self):
        res = self._capture_after_shift(acom._MOVE_TOL_CM)
        assert res["isError"] is False, res
        assert json.loads(res["content"][0]["text"])["captured"] is True

    def test_a_snap_back_just_over_the_tolerance_is_a_revert(self):
        res = self._capture_after_shift(acom._MOVE_TOL_CM * 1.001)
        assert res["isError"] is True
        assert "does NOT hold the pose you captured" in res["message"]

    def test_capture_with_nothing_pending_errors(self):
        _install([], pending=False)
        res = ja.handler(action="capture")
        assert res["isError"] is True
        assert "no pending" in res["message"].lower()

    def test_phantom_capture_bites(self):
        # add() returns a snapshot object but the count never advances -> error, not ok
        _, snaps, _, _ = _install([], pending=True)
        snaps.add = lambda: FakeSnapshot("Phantom")
        res = ja.handler(action="capture")
        assert res["isError"] is True
        assert "did not advance" in res["message"]

    def test_status_reports_pending_and_count(self):
        _install([], pending=True, snapshot_items=[FakeSnapshot()])
        out = _payload(ja.handler(action="status"))
        assert out["has_pending"] is True
        assert out["snapshot_count"] == 1

    def test_status_lists_captured_markers(self):
        _install([], snapshot_items=[FakeSnapshot("Position1", timeline_index=3),
                                     FakeSnapshot("Position2", timeline_index=5)])
        out = _payload(ja.handler(action="status"))
        assert out["markers"] == [{"name": "Position1", "timeline_index": 3},
                                  {"name": "Position2", "timeline_index": 5}]

    def test_delete_removes_named_marker(self):
        snap1, snap2 = FakeSnapshot("Position1"), FakeSnapshot("Position2")
        _, snaps, _, _ = _install([], snapshot_items=[snap1, snap2])
        out = _payload(ja.handler(action="delete", marker="Position1"))
        assert out["deleted"] is True and out["marker"] == "Position1"
        assert snap1.deleted is True
        assert snaps.count == 1
        assert snaps.item(0).name == "Position2"

    def test_delete_is_case_insensitive(self):
        snap = FakeSnapshot("Position1")
        _install([], snapshot_items=[snap])
        out = _payload(ja.handler(action="delete", marker="position1"))
        assert out["deleted"] is True

    def test_delete_declining_bool_errors(self):
        # Fusion declines the delete (deleteMe() returns False) -> error, not a false success.
        snap = FakeSnapshot("Position1", delete_ok=False)
        _install([], snapshot_items=[snap])
        res = ja.handler(action="delete", marker="Position1")
        assert res["isError"] is True
        assert "declined" in res["message"].lower()
        assert snap.deleted is False

    def test_delete_unknown_name_lists_candidates(self):
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        res = ja.handler(action="delete", marker="Ghost")
        assert res["isError"] is True
        assert "Position1" in res["message"] and "Position2" in res["message"]

    def test_delete_survivor_after_delete_is_error(self):
        # deleteMe() reports True but the snapshot is still in the collection on re-read -> error.
        snap = FakeSnapshot("Position1", survives_delete=True)
        _install([], snapshot_items=[snap])
        res = ja.handler(action="delete", marker="Position1")
        assert res["isError"] is True
        assert "still present" in res["message"].lower()

    def test_delete_with_an_unreadable_count_publishes_null_not_zero(self):
        # the count re-read RAISES after the delete: a fabricated 0 would tell the caller every
        # captured position is gone when a later marker is still standing.
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")],
                 blind_count_after_delete=True)
        out = _payload(ja.handler(action="delete", marker="Position1"))
        assert out["deleted"] is True
        assert out["snapshot_count"] is None
        assert "'snapshot_count' is null" in out["note"]

    def test_delete_publishes_the_count_it_read_back(self):
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        out = _payload(ja.handler(action="delete", marker="Position1"))
        assert out["snapshot_count"] == 1

    def test_delete_needs_marker_argument(self):
        _install([], snapshot_items=[FakeSnapshot("Position1")])
        res = ja.handler(action="delete", marker="")
        assert res["isError"] is True and "marker" in res["message"].lower()

    def test_delete_with_no_snapshots_errors(self):
        _install([], snapshot_items=[])
        res = ja.handler(action="delete", marker="Position1")
        assert res["isError"] is True and "nothing to delete" in res["message"].lower()

    def test_revert_deletes_latest_snapshot(self):
        snap = FakeSnapshot()
        _, snaps, _, _ = _install([], snapshot_items=[snap])
        out = _payload(ja.handler(action="revert"))
        assert snap.deleted is True
        assert out["reverted"] is True

    def test_revert_that_KEEPS_the_marker_is_an_error(self):
        # The lying delete: deleteMe() answers True while the marker stays in the collection.
        # Gating on the bool alone publishes reverted:true for a timeline nothing left; here both
        # re-reads convict it, and the refusal states each one it took.
        snap = FakeSnapshot("Position1", survives_delete=True)
        _install([], snapshot_items=[snap])
        res = ja.handler(action="revert")
        assert res["isError"] is True
        assert "still in the snapshot collection" in res["message"]
        assert "did not drop" in res["message"]
        assert "1 before, 1 after" in res["message"]

    def test_a_surviving_marker_is_caught_even_when_the_COUNT_DROPPED(self):
        # The narrower state no count comparison sees: the collection gave up a DIFFERENT marker,
        # so the count falls exactly as a working revert's does (2 before, 1 after) while the one
        # this arm deleted is still standing. The count gate passes it; the object the arm HELD,
        # read back by identity, is the only read that convicts it.
        other = FakeSnapshot("Position1")
        latest = FakeSnapshot("Position2", deletes_instead=other)
        _install([], snapshot_items=[other, latest])
        res = ja.handler(action="revert")
        assert res["isError"] is True
        assert "still in the snapshot collection" in res["message"]
        assert "was not removed" in res["message"]
        assert "did not drop" not in res["message"]      # the count gate passed it, as it must

    def test_a_revert_whose_count_will_not_read_says_the_marker_could_not_be_looked_for(self):
        # BOTH confirming reads run off the collection's own count (_common.iter_collection ranges
        # over it), so a count that will not re-read leaves the survivor check unable to run at
        # all. The receipt says that too - 'snapshot_count is null' alone would let a caller read
        # the marker as looked for and not found.
        snap = FakeSnapshot("Position1", survives_delete=True)
        _install([], snapshot_items=[snap], blind_count_after_any_delete=True)
        out = _payload(ja.handler(action="revert"))
        assert out["snapshot_count"] is None
        assert "the marker could not be looked for either" in out["note"]

    def test_revert_reads_back_the_object_it_DELETED_not_a_marker_of_the_same_name(self):
        # The re-read is by IDENTITY, not by name: Fusion enforces no uniqueness on marker names,
        # so a second marker wearing the deleted one's name makes a name-keyed re-read refuse a
        # removal that took. The count DID drop here, so nothing else in this arm can refuse.
        twin, gone = FakeSnapshot("Position1"), FakeSnapshot("Position1")
        _install([], snapshot_items=[twin, gone])
        out = _payload(ja.handler(action="revert"))
        assert out["reverted"] is True and gone.deleted is True and twin.deleted is False
        assert out["snapshot_count"] == 1

    def test_the_revert_note_does_not_claim_the_joint_defined_state_with_markers_LEFT(self):
        # Dropping the latest of several markers does not put the assembly back at the
        # joint-defined state - that is where it lands only when nothing else was ever captured,
        # which is how the discard arm words the same restore target.
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        out = _payload(ja.handler(action="revert"))
        assert "the last captured position that remains" in out["note"]
        assert "(back to the joint-defined state)" not in out["note"]
        assert "when nothing else was ever captured" in out["note"]

    def test_a_revert_after_which_the_count_GREW_labels_the_two_numbers(self):
        # The equal-count refusal reads the same whichever way round its two numbers are printed,
        # so it cannot pin the labels. A count that moved the other way can: the number taken
        # BEFORE the delete is 1 and the one read back after is 2, and a receipt that swaps them
        # sends the caller looking for a marker that never existed.
        _install([], snapshot_items=[FakeSnapshot("Position1", spawns_on_delete=True)])
        res = ja.handler(action="revert")
        assert res["isError"] is True
        assert "did not drop" in res["message"]
        assert "1 before, 2 after" in res["message"]

    def test_revert_that_drops_the_count_by_one_is_accepted(self):
        # The other side of that comparison's boundary: one fewer than before IS the removal, so a
        # gate written as "did not change" rather than "did not drop" would refuse a working revert.
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        out = _payload(ja.handler(action="revert"))
        assert out["reverted"] is True and out["snapshot_count"] == 1

    def test_revert_with_an_unreadable_count_publishes_null_not_the_arithmetic(self):
        # the count re-read RAISES after the delete: publishing "one fewer than before" would hand
        # the caller a number the tool computed, not one it read off the collection.
        snap = FakeSnapshot()
        _install([], snapshot_items=[snap], blind_count_after_delete=True)
        out = _payload(ja.handler(action="revert"))
        assert out["reverted"] is True
        assert out["snapshot_count"] is None
        assert "'snapshot_count' is null" in out["note"]

    def test_revert_publishes_the_count_it_read_back(self):
        _install([], snapshot_items=[FakeSnapshot("Position1"), FakeSnapshot("Position2")])
        out = _payload(ja.handler(action="revert"))
        assert out["snapshot_count"] == 1
        assert "null" not in out["note"]

    def test_revert_with_no_snapshots_errors(self):
        _install([], snapshot_items=[])
        res = ja.handler(action="revert")
        assert res["isError"] is True and "no captured" in res["message"].lower()

    def test_discard_pending_throws_the_uncaptured_move_away(self):
        snap = FakeSnapshot("Position1")
        _, snaps, _, _ = _install([], pending=True, snapshot_items=[snap])
        out = _payload(ja.handler(action="discard_pending"))
        assert out["discarded"] is True
        assert out["has_pending"] is False
        assert snaps.hasPendingSnapshot is False
        # discarding the PENDING move is not reverting a CAPTURED one - the marker survives
        assert snap.deleted is False
        assert out["snapshot_count"] == 1

    def test_discard_with_an_unreadable_count_publishes_null_not_the_pre_read(self):
        # the count read RAISES after the discard: echoing the count taken BEFORE the call would
        # publish a stale number as a fresh read-back.
        snap = FakeSnapshot("Position1")
        _install([], pending=True, snapshot_items=[snap], blind_count_after_discard=True)
        out = _payload(ja.handler(action="discard_pending"))
        assert out["discarded"] is True
        assert out["snapshot_count"] is None
        assert "'snapshot_count' is null" in out["note"]

    def test_discard_pending_with_nothing_pending_errors_without_calling_the_api(self):
        _, snaps, _, _ = _install([], pending=False)
        res = ja.handler(action="discard_pending")
        assert res["isError"] is True and "nothing to discard" in res["message"].lower()
        assert snaps.reverted_pending is False

    def test_discard_pending_declining_bool_errors(self):
        # revertPendingSnapshot() returns false -> the move still stands; never a false success.
        _install([], pending=True, revert_pending_ok=False)
        res = ja.handler(action="discard_pending")
        assert res["isError"] is True and "declined" in res["message"].lower()

    def test_discard_pending_that_leaves_the_flag_set_is_an_error(self):
        # the platform returns True while the pending change survives - the re-read must catch it.
        _install([], pending=True, revert_pending_lies=True)
        res = ja.handler(action="discard_pending")
        assert res["isError"] is True and "still" in res["message"].lower()

    def test_discard_pending_with_an_unreadable_flag_afterwards_is_an_error(self):
        # the confirming re-read RAISES: publishing has_pending False here would report a
        # measurement the tool never took, so the unreadable flag is an error, not a success.
        _install([], pending=True, blind_after_revert=True)
        res = ja.handler(action="discard_pending")
        assert res["isError"] is True
        assert "could not be re-read" in res["message"]
        assert "status" in res["message"]

    def test_unknown_action(self):
        _install([])
        res = ja.handler(action="frobnicate")
        assert res["isError"] is True and "must be one of" in res["message"]


class TestStatusPublishesTheFlagTriState:
    """has_pending is the flag itself, not a coerced boolean: True, False, or null when it could not
    be read. Publishing an unreadable flag as false would tell a caller "nothing is pending" on the
    one reading that supports no answer at all - and that caller then creates a joint through it."""

    def test_a_readable_true_publishes_true(self, as_built):
        as_built(pending=True)
        assert _payload(ja.handler(action="status"))["has_pending"] is True

    def test_a_readable_false_publishes_false(self, as_built):
        as_built(pending=False)
        assert _payload(ja.handler(action="status"))["has_pending"] is False

    def test_an_unreadable_flag_publishes_null_not_false(self, as_built):
        as_built(pending=False)
        ja._common.app.activeProduct.snapshots._blind = True
        out = _payload(ja.handler(action="status"))
        assert out["has_pending"] is None
        assert "UNKNOWN" in out["note"] and "not a 'no'" in out["note"]

    def test_the_markers_still_come_back_when_the_flag_is_unreadable(self, as_built):
        # the flag and the marker list are independent reads - losing one must not blank the other
        as_built(pending=False)
        design = ja._common.app.activeProduct
        design.snapshots._items = [FakeSnapshot("Position1", timeline_index=3)]
        design.snapshots._blind = True
        out = _payload(ja.handler(action="status"))
        assert out["markers"] == [{"name": "Position1", "timeline_index": 3}]

    def test_an_unreadable_flag_still_refuses_a_capture(self, as_built):
        # tri-state on the wire does not loosen the act precondition: only a real True may capture
        as_built(pending=False)
        ja._common.app.activeProduct.snapshots._blind = True
        res = ja.handler(action="capture")
        assert res["isError"] is True and "no pending" in res["message"].lower()

    def test_the_status_note_names_the_placement_exception(self, as_built):
        # a placement does NOT set this flag - a caller told otherwise chases a capture that refuses
        as_built(pending=True)
        note = _payload(ja.handler(action="status"))["note"]
        assert "design_add_instance placement does NOT" in note
