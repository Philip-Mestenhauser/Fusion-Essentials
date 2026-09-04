"""Unit tests for ``joint_create_as_built.py`` - jointing two occurrences where they already are.

Pinned here, no live Fusion: the geometry precondition (a non-rigid as-built joint RAISES with a
null geometry, so it is refused first), the motion read-back (a motion that silently comes back
rigid is a wrong result with a healthy feature), the post-creation rename, and the pending-move
refusal every joint CREATE shares.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

ja = load_tool("joint_create_as_built")
jn = load_tool("_joints")
jc = load_tool("assembly_capture_position")   # the other consumer of the shared pending read


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


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── joint_create_as_built ───────────────────────────────────────────────────────────

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
        monkeypatch.setattr(ja, "_resolve_input",
                            lambda d, spec: (f"JG[{spec}]", f"snap:{spec}", None))
        return abj
    return _make


class TestAsBuiltJoint:
    def test_rigid_as_built_passes_null_geometry(self, as_built):
        abj = as_built()
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
        o1, o2, geom = abj.last
        assert o1.name == "A:1" and o2.name == "B:1"
        assert geom is None                      # rigid as-built = null geometry
        assert out["created"] is True and out["joint_type"] == "rigid"
        # rigid sets no motion at all: null geometry IS the rigid contract
        assert abj.last_input.motion_calls == []

    def test_missing_occurrence_errors(self, as_built):
        as_built(occ_specs=(("A:1", "A:1"),))
        res = ja.handler(occurrence_one="A:1", occurrence_two="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_requires_two_distinct(self, as_built):
        as_built(occ_specs=(("A:1", "A:1"),))
        res = ja.handler(occurrence_one="A:1", occurrence_two="A:1")
        assert res["isError"] is True and "two distinct" in res["message"].lower()

    def test_same_local_name_different_path_is_allowed(self, as_built):
        # Two DISTINCT instances of the same component share a local .name ("Bolt:1") but
        # differ by fullPathName. The distinctness check must compare fullPathName, not .name - else it
        # false-positives and rejects a legitimate pair. Address each by its unambiguous fullPathName.
        abj = as_built(occ_specs=(("Bolt:1", "SubA/Bolt:1"), ("Bolt:1", "SubB/Bolt:1")))
        out = _payload(ja.handler(
            occurrence_one="SubA/Bolt:1", occurrence_two="SubB/Bolt:1"))
        assert out["created"] is True
        o1, o2, _ = abj.last
        assert o1.fullPathName == "SubA/Bolt:1" and o2.fullPathName == "SubB/Bolt:1"

    def test_payload_names_each_occurrence_by_full_path(self, as_built):
        # A NESTED child's .name is only the leaf ("Inner:1") - the caller addressed it as
        # "Outer:1+Inner:1", and only the full path names it unambiguously, so that is what the
        # payload publishes.
        as_built(occ_specs=(("Base:1", "Base:1"), ("Inner:1", "Outer:1+Inner:1")))
        out = _payload(ja.handler(
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
        return ja.handler(**args)

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
        res = ja.handler(occurrence_one="A:1", occurrence_two="B:1",
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
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
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
        return _payload(ja.handler(**args))

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
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["note"] == "Occurrences rigidly joined where they already are."


class TestAsBuiltName:
    """AsBuiltJoints.createInput/add take no name, so a requested name is applied to the CREATED
    joint through AsBuiltJoint.name and confirmed by reading it back."""

    def test_name_is_applied_after_creation_and_published(self, as_built):
        as_built()
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1",
                                                 name="Slider_R"))
        assert out["joint"] == "Slider_R"

    def test_no_name_leaves_the_joint_named_by_fusion(self, as_built):
        as_built()
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["joint"] == "AsBuilt1"

    def test_blank_name_is_not_a_rename_attempt(self, as_built):
        # "   " is no name at all; treating it as one would rename the joint to whitespace
        as_built(name_sticks=False)
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1",
                                                 name="   "))
        assert out["joint"] == "AsBuilt1"

    def test_a_name_that_does_not_take_is_refused_and_says_the_joint_exists(self, as_built):
        # the SWIG accept-and-ignore: nothing raises, so only the read-back catches it. Reporting
        # created:true with the requested name would publish a name the browser does not show.
        as_built(name_sticks=False)
        res = ja.handler(occurrence_one="A:1", occurrence_two="B:1", name="Slider_R")
        assert res["isError"] is True
        assert "Slider_R" in res["message"] and "AsBuilt1" in res["message"]
        assert "WAS created" in res["message"]
        assert "design_delete_feature" in res["message"]


class TestAsBuiltDescription:
    def test_description_states_the_no_parameter_fact_and_the_parametric_path(self):
        # fusion.AsBuiltJoint carries no offset/angle ModelParameter at all, so an as-built joint's
        # position can never be driven by an expression - the agent has to know that BEFORE it
        # builds the mechanism, not after joint_edit refuses.
        desc = ja.TOOL_DESCRIPTION
        assert "NO offset/angle ModelParameter" in desc
        assert "use joint_create when it must be parametric" in desc


class TestAsBuiltPendingMoveRefusal:
    """An as-built joint says "joint them where they are" - but its creation recomputes the assembly,
    and a recompute REVERTS an uncaptured occurrence position, so "where they are" becomes the
    reverted pose. The create refuses while the pending flag is set, naming the same remedy
    assembly_capture_position offers, and the flag comes from the ONE shared read."""

    def test_refuses_the_as_built_create_while_a_move_is_pending(self, as_built):
        abj = as_built(pending=True)
        res = ja.handler(occurrence_one="A:1", occurrence_two="B:1")
        assert res["isError"] is True
        assert "would silently revert" in res["message"]
        assert abj.added == 0                       # refused before asBuiltJoints.add

    def test_the_refusal_names_capture_and_discard(self, as_built):
        as_built(pending=True)
        msg = ja.handler(occurrence_one="A:1", occurrence_two="B:1")["message"]
        assert "assembly_capture_position(action='capture')" in msg
        assert "action='discard_pending'" in msg

    def test_creates_normally_with_nothing_pending(self, as_built):
        abj = as_built(pending=False)
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["created"] is True and abj.added == 1

    def test_an_unreadable_pending_flag_does_not_refuse(self, as_built):
        # The flag RAISES - unknown, not pending. An unreadable flag is no evidence of a move, so it
        # must not block a create the way a real True does.
        abj = as_built(pending=False)
        design = ja._common.app.activeProduct
        design.snapshots._blind = True
        out = _payload(ja.handler(occurrence_one="A:1", occurrence_two="B:1"))
        assert out["created"] is True and abj.added == 1

    def test_capture_status_consumes_the_shared_read(self, as_built, monkeypatch):
        # The bite for the ONE-home claim: stub the shared read alone. An inlined
        # snaps.hasPendingSnapshot re-roll in the status handler would ignore this and answer False.
        as_built(pending=False)
        monkeypatch.setattr(jn, "pending_position", lambda design: True)
        out = _payload(jc.handler(action="status"))
        assert out["has_pending"] is True

    def test_the_create_guard_consumes_the_same_shared_read(self, as_built, monkeypatch):
        as_built(pending=False)
        monkeypatch.setattr(jn, "pending_position", lambda design: True)
        res = ja.handler(occurrence_one="A:1", occurrence_two="B:1")
        assert res["isError"] is True and "would silently revert" in res["message"]
