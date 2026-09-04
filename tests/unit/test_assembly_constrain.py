"""Unit tests for ``assembly_constrain.py`` - Constrain Components over a relationship SET.

Pinned here, no live Fusion: the '<occurrence>:<snap>' grammar, the value encoding (offset is a
cm-scaled length, an angle a 'deg' string), and what the create VERIFIES - the constraint solved,
it broke no existing timeline feature, it holds every relationship submitted, and which parts it
repositioned.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

ja = load_tool("assembly_constrain")


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


def timeline_item(name, health=0):
    """One parametric-timeline item as _common.timeline_health reads it: a name plus a healthState
    (0 healthy / 1 warning / 2 error) a test can flip to model the recompute breaking it."""
    return SimpleNamespace(name=name, healthState=health)


def fake_timeline(items):
    """A count/item(i) timeline over 'items' - the collection protocol timeline_health walks."""
    return SimpleNamespace(count=len(items), item=lambda i: items[i])


def poison_timeline_item(name):
    """Returns (item, reads): a timeline entry whose healthState RAISES, appending to 'reads' first.
    This is a freshly added assembly constraint's own entry - measured raising '1 : Unknown exception'
    right after the add, and the same caught error inside a script context rolled the whole
    transaction back, so the assertion worth making is that nothing read it at all."""
    reads = []

    def _health(self):
        reads.append(name)
        raise RuntimeError("1 : Unknown exception")
    return type("T", (), {"name": name, "healthState": property(_health)})(), reads


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
    ja.app = type("A", (), {"activeProduct": design})()
    ja._common.app = ja.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design, snaps, abj, ac


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── assembly_constrain ──────────────────────────────────────────────────────

class TestAssemblyConstraint:
    def test_missing_occurrence_errors(self):
        _install(["A:1"])
        res = ja.handler(occurrence_one="Ghost", occurrence_two="A:1")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_resolves_both_occurrences(self):
        # With no snaps and no selection, the handler should ask for geometry, not crash.
        _install(["A:1", "B:1"])
        res = ja.handler(occurrence_one="A:1", occurrence_two="B:1")
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
        out = _payload(ja.handler(
            snap_one="TrussMast:1:top", snap_two="Boom:1:bottom", offset=0))
        # a relationship was added with the two resolved entities
        rels = ac.last_input.geometricRelationships.added
        assert len(rels) == 1
        e1, e2 = rels[0][0], rels[0][1]
        assert e1 == "ENT[TrussMast:1:top]" and e2 == "ENT[Boom:1:bottom]"
        assert out["created"] is True

    def test_flip_defaults_false(self, monkeypatch):
        design, ac = self._install_with_snaps(monkeypatch)
        ja.handler(snap_one="A:1:top", snap_two="B:1:top",
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
        res = ja.handler(snap_one="A:1:top", snap_two="A:1:bottom")
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
        out = _payload(ja.handler(relationships=[
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
        ja.handler(relationships=[
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
        out = _payload(ja.handler(snap_one="A:1:top", snap_two="B:1:top"))
        assert out["relationship_count"] == 1

    def test_bad_relationship_item_errors(self, monkeypatch):
        design, snaps, abj, ac = _install(["A:1"])
        self._stub(monkeypatch, design)
        res = ja.handler(relationships=[{"snap_one": "A:1:top"}])  # missing snap_two
        assert res["isError"] is True
        assert "snap_two" in res["message"]

    def test_relationships_must_be_a_list(self, monkeypatch):
        # passing a non-list (e.g. a dict or string) must error cleanly, not iterate chars/keys.
        design, snaps, abj, ac = _install(["A:1"])
        self._stub(monkeypatch, design)
        res = ja.handler(relationships={"snap_one": "A:1:top", "snap_two": "A:1:bottom"})
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
        ja.handler(snap_one="A:1:top", snap_two="B:1:top", offset=10, units="mm")
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("real", 1.0)        # 10 mm -> 1.0 cm via createByReal

    def test_offset_inch_scaling(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.handler(snap_one="A:1:top", snap_two="B:1:top", offset=2, units="in")
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value[0] == "real" and abs(value[1] - 5.08) < 1e-9   # 2 in -> 5.08 cm

    def test_unknown_units_errors_not_silently_treated_as_mm(self, monkeypatch):
        # An unrecognized unit must be REFUSED, not silently treated as mm, like joint_create errors
        # on the same bad input.
        self._stub(monkeypatch)
        res = ja.handler(snap_one="A:1:top", snap_two="B:1:top",
                                             offset=10, units="furlong")
        assert res["isError"] is True
        assert "furlong" in res["message"]

    def test_angle_uses_deg_string_not_offset(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.handler(relationships=[
            {"snap_one": "A:1:right", "snap_two": "B:1:left", "angle_deg": 30}])
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("string", "30.0 deg")  # angle path -> createByString, NOT a cm offset

    def test_zero_offset_is_real_zero(self, monkeypatch):
        ac = self._stub(monkeypatch)
        ja.handler(snap_one="A:1:top", snap_two="B:1:top", offset=0)
        value = ac.last_input.geometricRelationships.added[0][3]
        assert value == ("real", 0.0)


# ── what the create VERIFIES: the constraint solved, it broke nothing else, and who moved ───────
# A constraint that is ADDED is not a constraint that WORKS: the platform hands back a constraint
# object whose healthState can read warning/error, the recompute the add triggers can leave EXISTING
# joints unhealthy, and the parts it is supposed to locate may not have moved at all.

@pytest.fixture
def constrain(monkeypatch):
    """Factory: install a design for assembly_constrain - occurrences with readable world positions, a
    configurable assemblyConstraints collection, and an optional timeline whose items the add can
    break. Stubs the shared snap resolver so a '<occ>:<snap>' pair yields an opaque entity (a real
    BRep proxy needs a live session). Returns (design, assemblyConstraints)."""
    def _make(occ_specs=(("A:1", (0.0, 0.0, 0.0)), ("B:1", (0.0, 0.0, 0.0))), timeline_items=None,
              **ac_kwargs):
        import adsk.fusion
        ac = FakeAssemblyConstraints(**ac_kwargs)
        timeline = fake_timeline(timeline_items) if timeline_items is not None else None
        # a spec is (name, pos) or (name, pos, fullPathName) - the third form is a NESTED occurrence,
        # whose leaf name differs from the path that names it uniquely
        occs = [FakeOcc(s[0], full_path=(s[2] if len(s) > 2 else None), pos=s[1]) for s in occ_specs]
        design = FakeDesign(occs, FakeSnapshots(), FakeAsBuiltJoints(), ac, timeline=timeline)
        fake_app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(ja, "app", fake_app)
        monkeypatch.setattr(ja._common, "app", fake_app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        monkeypatch.setattr(ja, "_resolve_snap_entity",
                            lambda d, occ, snap: (f"ENT[{occ}:{snap}]", "planar", None))
        return design, ac
    return _make


def _constrain_pair(**kw):
    args = {"snap_one": "A:1:top", "snap_two": "B:1:bottom"}
    args.update(kw)
    return ja.handler(**args)


class TestConstraintSolveState:
    """healthState on the created constraint: 2 = error, 1 = warning - the pair assembly_get's
    relations slice publishes as healthy:false. Either one means the constraint is not locating the
    parts, and an UNREADABLE state means nothing here says it is."""

    def test_failed_solve_is_refused_naming_the_delete_path(self, constrain):
        constrain(health=2, message="over-constrained")
        res = _constrain_pair()
        assert res["isError"] is True
        assert "FAILED to solve" in res["message"]
        assert "over-constrained" in res["message"]
        # the constraint is still in the design, so the refusal has to name how to get rid of it
        assert "assembly_edit_relations" in res["message"]
        assert "name='Constraint1'" in res["message"] and "action='delete'" in res["message"]

    def test_compute_warning_is_refused_too(self, constrain):
        # a warning state is what assembly_get reports as healthy:false - reporting created:true here
        # would claim the parts are located on the one reading that says they are not
        constrain(health=1)
        res = _constrain_pair()
        assert res["isError"] is True
        assert "compute WARNING" in res["message"]
        assert "action='delete'" in res["message"]

    def test_unreadable_health_state_is_refused_as_unconfirmed(self, constrain):
        # healthState RAISES: publishing created:true would report a solve nobody read
        constrain(blind_health=True)
        res = _constrain_pair()
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"]
        assert "Constraint1" in res["message"] and "assembly_get" in res["message"]
        assert "action='delete'" in res["message"]

    def test_a_healthy_constraint_is_reported_created(self, constrain):
        constrain(health=0)
        out = _payload(_constrain_pair())
        assert out["created"] is True and out["constraint"] == "Constraint1"


class TestConstraintCollateralDamage:
    """The add recomputes the assembly, and that recompute can break joints/motion links the parts
    already carried. Those go unhealthy under their OWN names, so only a before/after delta separates
    the damage this call did from what was already broken."""

    def test_relations_broken_by_the_add_are_named(self, constrain):
        rev1, rev2, link = (timeline_item("Rev1"), timeline_item("Rev2"),
                            timeline_item("Link1"))
        _design, ac = constrain(timeline_items=[rev1, rev2, link])
        # the recompute leaves two joints in warning and the motion link in error
        def _break():
            rev1.healthState, rev2.healthState, link.healthState = 1, 1, 2
        ac.on_add = _break
        res = _constrain_pair()
        assert res["isError"] is True
        for name in ("Rev1", "Rev2", "Link1"):
            assert name in res["message"], name
        assert "3 existing" in res["message"]
        assert "action='delete'" in res["message"]

    def test_a_warning_only_break_is_not_swallowed(self, constrain):
        # the measured damage reads as a compute WARNING, not an error - an errors-only delta would
        # report this add as a clean success
        rev1 = timeline_item("Rev1")
        _design, ac = constrain(timeline_items=[rev1])
        ac.on_add = lambda: setattr(rev1, "healthState", 1)
        res = _constrain_pair()
        assert res["isError"] is True and "Rev1" in res["message"]

    def test_a_feature_already_unhealthy_is_not_blamed_on_this_add(self, constrain):
        # it was broken BEFORE the add - refusing here would make the tool unusable on a design that
        # already carries a warning
        constrain(timeline_items=[timeline_item("Rev1", health=1),
                                  timeline_item("Rev2", health=2)])
        out = _payload(_constrain_pair())
        assert out["created"] is True

    def test_a_clean_timeline_reports_created(self, constrain):
        constrain(timeline_items=[timeline_item("Rev1"), timeline_item("Rev2")])
        out = _payload(_constrain_pair())
        assert out["created"] is True

    def test_the_constraint_does_not_blame_itself(self, constrain):
        # the constraint's own timeline entry carries its name, so an unhealthy entry named like the
        # constraint is the constraint - counting it would make a healthy add refuse itself
        own = timeline_item("Constraint1")
        _design, ac = constrain(timeline_items=[own])
        ac.on_add = lambda: setattr(own, "healthState", 2)
        out = _payload(_constrain_pair())
        assert out["created"] is True

    def test_the_fresh_timeline_entry_is_never_read(self, constrain):
        # its healthState is a POISON READ (raises, and the caught error rolled back the whole
        # transaction inside a script context), so the walk stops at the pre-add item count
        rev1 = timeline_item("Rev1")
        design, ac = constrain(timeline_items=[rev1])
        poison, reads = poison_timeline_item("Constraint1")

        def _add_entry():
            design.timeline = fake_timeline([rev1, poison])
        ac.on_add = _add_entry
        out = _payload(_constrain_pair())
        assert out["created"] is True
        assert reads == []                     # the new entry was never touched, not merely survived


class TestConstraintRelationshipCount:
    """relationship_count is a READ off the created constraint or it is null - never the request. A
    request echoed as a count reports the ask back as a measurement."""

    def test_the_count_is_read_off_the_constraint(self, constrain):
        constrain()
        out = _payload(ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"}]))
        assert out["relationship_count"] == 2
        assert out["relationships_submitted"] == 2

    def test_an_unreadable_count_publishes_null_not_the_request(self, constrain):
        constrain(blind_count=True)
        out = _payload(ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"}]))
        assert out["relationship_count"] is None
        assert out["relationships_submitted"] == 2
        assert "'relationship_count' is null" in out["note"]

    def test_a_count_short_of_the_request_is_refused(self, constrain):
        # the constraint holds ONE relationship where three were submitted: the two missing pairs
        # constrain nothing, so the parts are not located the way the call describes. The sibling
        # rigid group refuses the same shortfall; a note-only disclosure would ship created:true.
        constrain(count=1)
        res = ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"},
            {"snap_one": "A:1:back", "snap_two": "B:1:back"}])
        assert res["isError"] is True
        assert "only 1 of the 3" in res["message"]
        assert "action='delete'" in res["message"]      # it REMAINS - name the removal path

    def test_one_short_of_the_request_is_refused(self, constrain):
        # the N-1 boundary: two landed of three submitted is still a shortfall
        constrain(count=2)
        res = ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"},
            {"snap_one": "A:1:back", "snap_two": "B:1:back"}])
        assert res["isError"] is True
        assert "only 2 of the 3" in res["message"]

    def test_a_full_landing_is_created(self, constrain):
        # the other side of the boundary: N of N submitted stays ok, with no shortfall wording
        constrain(count=3)
        out = _payload(ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"},
            {"snap_one": "A:1:back", "snap_two": "B:1:back"}]))
        assert out["created"] is True and out["relationship_count"] == 3
        assert "of the 3" not in out["note"]

    def test_a_count_above_the_request_is_disclosed_not_refused(self, constrain):
        # a surplus is not a shortfall: nothing the caller asked for is missing, so it is said out
        # loud in the note and the constraint stands
        constrain(count=3)
        out = _payload(ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"}]))
        assert out["created"] is True
        assert "reads 3 for the 2" in out["note"]

    def test_an_unreadable_count_is_not_treated_as_a_shortfall(self, constrain):
        # None is not "fewer than submitted" - it is unknown, and refusing on it would fail every
        # call on a build whose count cannot be read
        constrain(blind_count=True)
        out = _payload(ja.handler(relationships=[
            {"snap_one": "A:1:bottom", "snap_two": "B:1:top"},
            {"snap_one": "A:1:left", "snap_two": "B:1:left"}]))
        assert out["created"] is True and out["relationship_count"] is None


class TestConstraintMovedVerdict:
    """The tool's job is LOCATING parts, so the payload says whether a part moved: a distance per
    repositioned occurrence, an empty list when nothing moved, null when no position could be sampled
    (an unread transform is not a 'no')."""

    def test_a_repositioned_part_is_published_with_its_distance(self, constrain):
        design, ac = constrain()
        moving = design.rootComponent.allOccurrences[1]
        ac.on_add = lambda: setattr(moving, "pos", (5.0, 0.0, 0.0))   # 5 cm = 50 mm
        out = _payload(_constrain_pair())
        assert out["moved"] == [{"occurrence": "B:1", "distance_mm": 50.0,
                                 "direction": [1.0, 0.0, 0.0]}]
        assert "B:1 by 50.0 mm" in out["note"]

    def test_nothing_moved_publishes_an_empty_list_and_says_so(self, constrain):
        constrain()
        out = _payload(_constrain_pair())
        assert out["moved"] == []
        assert "NO target occurrence moved" in out["note"]

    def test_unreadable_positions_publish_null_not_an_empty_list(self, constrain):
        # both transforms RAISE: "nothing moved" would be a claim about a reading nobody took
        constrain(occ_specs=(("A:1", None), ("B:1", None)))
        out = _payload(_constrain_pair())
        assert out["moved"] is None
        assert "UNKNOWN" in out["note"] and "not a 'no'" in out["note"]

    def test_both_fields_label_a_nested_part_by_its_full_path(self, constrain):
        # 'occurrences' and the moved rows are ONE labelling scheme, or a caller cannot correlate them:
        # the snap string carries the leaf name, the payload publishes the unique path
        design, ac = constrain(occ_specs=(("Base:1", (0.0, 0.0, 0.0)),
                                          ("Inner:1", (0.0, 0.0, 0.0), "Outer:1+Inner:1")))
        nested = design.rootComponent.allOccurrences[1]
        ac.on_add = lambda: setattr(nested, "pos", (0.0, 0.0, 1.0))
        out = _payload(ja.handler(snap_one="Base:1:top",
                                                      snap_two="Inner:1:bottom"))
        assert out["occurrences"] == ["Base:1", "Outer:1+Inner:1"]
        assert [m["occurrence"] for m in out["moved"]] == ["Outer:1+Inner:1"]

    def test_both_constrained_parts_are_sampled(self, constrain):
        design, ac = constrain()
        a, b = design.rootComponent.allOccurrences
        def _both():
            a.pos = (0.0, 1.0, 0.0)
            b.pos = (0.0, 0.0, 2.0)
        ac.on_add = _both
        out = _payload(_constrain_pair())
        assert sorted(m["occurrence"] for m in out["moved"]) == ["A:1", "B:1"]
        assert {m["distance_mm"] for m in out["moved"]} == {10.0, 20.0}
