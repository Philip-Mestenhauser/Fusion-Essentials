"""Unit tests for ``joints_advanced.py`` — assembly_capture_position, joint_create_as_built, assembly_constrain.

Tests written BEFORE further wiring (project rule). The nuances pinned, no live
Fusion:

  assembly_capture_position — the timeline pose mechanic. Capture is only valid when a move
  is pending (Design.snapshots.hasPendingSnapshot); 'revert' deletes the latest
  snapshot back to the joint-defined state; 'status' reports pending + count.

  joint_create_as_built — a joint where parts ALREADY are; createInput(occ1, occ2, None)
  for a rigid as-built; occurrences resolved by name.

  assembly_constrain — the new Constrain Components: build geometric relationships
  between two occurrences' entities (type inferred: flush/coincident/concentric/
  angle). Here we pin the occurrence resolution + input assembly, with geometry
  entities supplied as opaque tokens (real BRep proxies need a live session).
"""

import json

from conftest import load_tool

ja = load_tool("assembly_joints_advanced")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeSnapshot:
    def __init__(self, name="Snapshot1"):
        self.name = name
        self.deleted = False

    def deleteMe(self):
        self.deleted = True
        return True


class FakeSnapshots:
    def __init__(self, pending=False, items=()):
        self.hasPendingSnapshot = pending
        self._items = list(items)
        self.added = False

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def add(self):
        self.added = True
        snap = FakeSnapshot(f"Snapshot{len(self._items) + 1}")
        self._items.append(snap)
        self.hasPendingSnapshot = False
        return snap


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name


class FakeAsBuiltInput:
    pass


class FakeAsBuiltJoints:
    def __init__(self):
        self.last = None

    def createInput(self, o1, o2, geometry):
        self.last = (o1, o2, geometry)
        return FakeAsBuiltInput()

    def add(self, inp):
        return type("J", (), {"name": "AsBuilt1"})()


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


def _install(occ_names, pending=False, snapshot_items=()):
    snaps = FakeSnapshots(pending=pending, items=snapshot_items)
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

    def test_status_reports_pending_and_count(self):
        _install([], pending=True, snapshot_items=[FakeSnapshot()])
        out = _payload(ja.capture_position_handler(action="status"))
        assert out["has_pending"] is True
        assert out["snapshot_count"] == 1

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

    def test_unknown_action(self):
        _install([])
        res = ja.capture_position_handler(action="frobnicate")
        assert res["isError"] is True and "Unknown action" in res["message"]


# ── joint_create_as_built ───────────────────────────────────────────────────────────

class TestAsBuiltJoint:
    def test_rigid_as_built_passes_null_geometry(self):
        _, _, abj, _ = _install(["A:1", "B:1"])
        out = _payload(ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="B:1"))
        o1, o2, geom = abj.last
        assert o1.name == "A:1" and o2.name == "B:1"
        assert geom is None                      # rigid as-built = null geometry
        assert out["created"] is True

    def test_missing_occurrence_errors(self):
        _install(["A:1"])
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_requires_two_distinct(self):
        _install(["A:1"])
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="A:1")
        assert res["isError"] is True and "two distinct" in res["message"].lower()

    def test_same_local_name_different_path_is_allowed(self):
        # PR-review #8: two DISTINCT instances of the same component share a local .name ("Bolt:1") but
        # differ by fullPathName. The distinctness check must compare fullPathName, not .name — else it
        # false-positives and rejects a legitimate pair. Address each by its unambiguous fullPathName.
        snaps = FakeSnapshots(pending=False, items=())
        abj, ac = FakeAsBuiltJoints(), FakeAssemblyConstraints()
        occs = [FakeOcc("Bolt:1", full_path="SubA/Bolt:1"),
                FakeOcc("Bolt:1", full_path="SubB/Bolt:1")]
        design = FakeDesign(occs, snaps, abj, ac)
        ja.app = type("A", (), {"activeProduct": design})()
        ja._common.app = ja.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
        out = _payload(ja.as_built_joint_handler(
            occurrence_one="SubA/Bolt:1", occurrence_two="SubB/Bolt:1"))
        assert out["created"] is True
        o1, o2, _ = abj.last
        assert o1.fullPathName == "SubA/Bolt:1" and o2.fullPathName == "SubB/Bolt:1"

    def test_same_object_twice_still_rejected(self):
        # The identity fallback: passing the SAME occurrence (same fullPathName) twice is still rejected.
        _install(["A:1"])
        res = ja.as_built_joint_handler(occurrence_one="A:1", occurrence_two="A:1")
        assert res["isError"] is True and "two distinct" in res["message"].lower()


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

    def test_snap_carries_offset_value(self, monkeypatch):
        design, ac = self._install_with_snaps(monkeypatch)
        ja.assembly_constraint_handler(snap_one="A:1:top", snap_two="B:1:top",
                                       offset=10, units="mm")
        # the 4th arg of add() is the ValueInput (offset); flipped is the 3rd
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
    """ONE constraint with MULTIPLE relationships solved together (Fusion's actual model) — the fix
    for the over-determined single-relationship skew."""

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
