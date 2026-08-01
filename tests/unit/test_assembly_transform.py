"""Unit tests for ``assembly_transform.py`` - occurrence ground/move + rigid group.

The logic pinned here, no live Fusion: occurrence resolution (exact, then substring;
missing reported), the ground_to_parent lock (isGroundToParent), assembly_move
building a Matrix3D translation/rotation and applying it to occurrence.transform2 (the
same property assembly_get's own read path prefers), and assembly_rigid_group
collecting occurrences into RigidGroups.add. Fakes expose the real attributes the
handlers set so we can assert on them.
"""

import json
import math

import pytest

from conftest import load_tool


asm = load_tool("assembly_transform")


# -- fakes ---------------------------------------------------------------------

class _Vec:
    """Vector3D subset for edge-axis derivation: length + normalize."""
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    @property
    def length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    def normalize(self):
        n = self.length
        self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
        return True


class _Pt:
    """Point3D subset: vectorTo, for deriving a direction from two points."""
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def vectorTo(self, other):
        return _Vec(other.x - self.x, other.y - self.y, other.z - self.z)


def _line3d(sp, ep):
    """The LIVE shape of a bounded edge's geometry: a Line3D with startPoint/endPoint ONLY
    (no .direction/.origin - those belong to InfiniteLine3D)."""
    import adsk.core
    return type("Line3D", (), {"curveType": adsk.core.Curve3DTypes.Line3DCurveType,
                               "startPoint": _Pt(*sp), "endPoint": _Pt(*ep)})()


class FakeMatrix:
    """Models the subset of Matrix3D the move handler touches: a settable 'translation' (Vector3D),
    transformBy (compose), and setToRotation.

    Records HOW each operation was applied: assigning `mat.translation = vec` on the SAME matrix
    that holds a rotation clobbers the pivot (whose pivot lives in the translation column), so
    `direct_translation_assigned_after_rotation` flags that; a translation composed as its OWN
    matrix via transformBy is tracked in `composed` instead."""
    def __init__(self):
        self._translation = None
        self.rotation = None
        # diagnostics for the pivot test:
        self.direct_translation_assigned_after_rotation = False
        self.composed = []             # matrices transformBy'd onto this one

    @property
    def translation(self):
        return self._translation

    @translation.setter
    def translation(self, v):
        # assigning the column directly AFTER a rotation is the bug (it overwrites the pivot column).
        if self.rotation is not None:
            self.direct_translation_assigned_after_rotation = True
        self._translation = v

    def setToRotation(self, angle, axis, origin):
        self.rotation = (angle, axis, origin)

    def transformBy(self, other):
        self.composed.append(other)
        # carry rotation + translation forward so the translate-only path still surfaces the vector.
        if getattr(other, "rotation", None) is not None:
            self.rotation = other.rotation
        ot = getattr(other, "translation", None)
        if ot is not None:
            self._translation = ot


class _FakeJointColl:
    def __init__(self, names):
        self._names = list(names)

    @property
    def count(self):
        return len(self._names)

    def item(self, i):
        return type("J", (), {"name": self._names[i]})()


class FakeOcc:
    def __init__(self, name, full_path=None, joints=()):
        self.name = name
        self.fullPathName = full_path or name
        self.isGrounded = False
        self.isGroundToParent = True
        self.transform = FakeMatrix()
        # transform2 is a SEPARATE property from transform (real Fusion API) - assembly_move reads/
        # writes transform2 (matching assembly_get's own read path); assembly_ground's position
        # reporting still reads transform. Modeled as independent fakes so a test using the wrong one
        # would fail instead of silently passing off a shared object.
        self.transform2 = FakeMatrix()
        self.transform_applied = None
        # the occurrence's joints collection (what the move-guard inspects)
        self.joints = _FakeJointColl(joints)

    # transform is settable
    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)


class FakeRigidGroup:
    def __init__(self, name="RigidGroup1"):
        self.name = name


class FakeRigidGroups:
    def __init__(self):
        self.last = None

    def add(self, occurrences, includeChildren):
        self.last = (occurrences, includeChildren)
        return FakeRigidGroup()


class FakeObjectCollection:
    def __init__(self):
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def add(self, x):
        self._items.append(x)


class FakeRoot:
    def __init__(self, occurrences, rg):
        self.allOccurrences = list(occurrences)
        self.rigidGroups = rg


class FakeDesign:
    def __init__(self, occurrences, rg):
        self.rootComponent = FakeRoot(occurrences, rg)


def _install(occ_names):
    rg = FakeRigidGroups()
    occs = [FakeOcc(n) for n in occ_names]
    design = FakeDesign(occs, rg)
    asm.app = type("A", (), {"activeProduct": design})()
    asm._common.app = asm.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ObjectCollection.create = staticmethod(FakeObjectCollection)
    # Matrix3D + Vector3D for move
    adsk.core.Matrix3D.create = staticmethod(FakeMatrix)
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    return design, occs, rg


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _occ(occs, name):
    return next(o for o in occs if o.name == name)


# -- ground ---------------------------------------------------------------------

class TestGround:
    # assembly_ground sets ONLY isGroundToParent (the stateless parent lock). The UI Ground/Fix
    # flag (isGrounded) is deliberately not settable - the platform treats it as legacy - but
    # both flags are reported back so the caller sees the full grounding state.
    def test_lock_to_parent(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=True))
        assert _occ(occs, "Block:1").isGroundToParent is True
        assert out["isGroundToParent"] is True

    def test_unground_from_parent_releases_lock(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=False))
        assert _occ(occs, "Block:1").isGroundToParent is False
        assert out["isGroundToParent"] is False

    def test_stuck_flag_bites(self):
        # the assignment is accepted but the flag still reads its prior value -> error, not ok
        _, occs, _ = _install(["Block:1"])

        class StuckOcc(FakeOcc):
            @property
            def isGroundToParent(self):
                return True

            @isGroundToParent.setter
            def isGroundToParent(self, v):
                pass

        occs[0].__class__ = StuckOcc
        res = asm.ground_handler(occurrence="Block:1", ground_to_parent=False)
        assert res["isError"] is True
        assert "did not take" in res["message"]

    def test_grounding_snap_back_is_reported_with_numbers(self):
        # Setting ground_to_parent=true snaps the part back to its timeline placement, discarding
        # free moves captured or not (live-verified) - the payload must report the snap, not let the
        # caller discover a teleported part later.
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        o.transform._translation = _Vec(2.0, 0.0, 0.0)      # cm: the moved pose (20 mm)

        class SnappingOcc(o.__class__):
            @property
            def isGroundToParent(self):
                return object.__getattribute__(self, "_g2p")

            @isGroundToParent.setter
            def isGroundToParent(self, v):
                object.__setattr__(self, "_g2p", v)
                if v:                                        # the platform snap-back
                    self.transform._translation = _Vec(0.0, 0.0, 0.0)

        o.__class__ = SnappingOcc
        o._g2p = False
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=True))
        assert out["position_reset"] == {"from_mm": [20.0, 0.0, 0.0], "to_mm": [0.0, 0.0, 0.0]}
        assert "SNAPPED" in out["position_warning"]

    def test_no_snap_reports_no_reset(self):
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        o.transform._translation = _Vec(2.0, 0.0, 0.0)       # position held through the flag set
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=True))
        assert "position_reset" not in out and "position_warning" not in out

    def test_isGrounded_is_never_written(self):
        # The tool sets ONLY isGroundToParent; the UI Ground/Fix flag (isGrounded) is not settable
        # through this surface and stays exactly as it was.
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        o.isGrounded = False
        asm.ground_handler(occurrence="Block:1", ground_to_parent=True)
        assert o.isGrounded is False              # untouched

    def test_no_grounded_param_is_rejected_by_strict_schema(self):
        # There is no 'grounded' input - only 'ground_to_parent'. (At the MCP boundary the strict
        # schema rejects it; at the Python level it's a TypeError.)
        _install(["Block:1"])
        import pytest
        with pytest.raises(TypeError):
            asm.ground_handler(occurrence="Block:1", grounded=True)

    def test_mode_input_is_gone(self):
        # There is no 'mode' selector - isGrounded is not reachable through any input. (Strict
        # schema at the MCP boundary; TypeError at the Python level.)
        _install(["Block:1"])
        import pytest
        with pytest.raises(TypeError):
            asm.ground_handler(occurrence="Block:1", ground_to_parent=True, mode="fixed")

    def test_reports_both_flags_distinctly(self):
        # the payload carries BOTH flags so the caller sees the full grounding state (a human may
        # have set isGrounded in the UI).
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=True))
        assert "isGroundToParent" in out and "isGrounded" in out
        assert out["isGroundToParent"] is True

    def test_substring_match(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="block", ground_to_parent=True))
        assert out["occurrence"] == "Block:1"

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.ground_handler(occurrence="Ghost", ground_to_parent=True)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_no_change_requested_errors(self):
        _install(["Block:1"])
        res = asm.ground_handler(occurrence="Block:1")
        assert res["isError"] is True and "ground_to_parent" in res["message"]

    def test_ambiguous_name_refused_not_wrong_instance(self):
        # The wrong-instance bug: two instances share the local name "Bolt:1" under different
        # sub-assemblies. A bare "Bolt" substring must ERROR (naming both fullPathNames), NOT silently
        # ground the first one.
        _, occs, _ = _install([])
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1"); a.isGroundToParent = False
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1"); b.isGroundToParent = False
        asm.app.activeProduct.rootComponent.allOccurrences = [a, b]
        res = asm.ground_handler(occurrence="Bolt", ground_to_parent=True)
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        # And neither was mutated (the ambiguous call refused before touching either).
        assert a.isGroundToParent is False and b.isGroundToParent is False

    def test_exact_full_path_targets_the_right_instance(self):
        _, occs, _ = _install([])
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1"); a.isGroundToParent = False
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1"); b.isGroundToParent = False
        asm.app.activeProduct.rootComponent.allOccurrences = [a, b]
        res = asm.ground_handler(occurrence="Sub-B:1+Bolt:1", ground_to_parent=True)
        assert res["isError"] is False
        assert b.isGroundToParent is True and a.isGroundToParent is False   # the RIGHT one


# -- assembly_move ---------------------------------------------------------------

class TestMove:
    def test_move_that_does_not_take_bites(self):
        # the transform assignment is accepted but the pose reads unchanged -> error, not ok
        _, occs, _ = _install(["Block:1"])

        class FrozenMatrix(FakeMatrix):
            def asArray(self):
                return (1.0,) * 16          # constant pose: the assignment never takes

        occs[0].transform2 = FrozenMatrix()
        res = asm.move_handler(occurrence="Block:1", dx=10)
        assert res["isError"] is True
        assert "did not move" in res["message"]

    def test_translate_sets_transform(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.move_handler(occurrence="Block:1", dx=10, dy=0, dz=5, units="mm"))
        o = _occ(occs, "Block:1")
        # a new transform2 matrix was assigned (transform2, not transform - matches assembly_get's
        # own read path)
        assert o.transform2 is not None
        assert out["moved"] is True
        assert out["translation"] == {"x": 10, "y": 0, "z": 5}

    def test_writes_transform2_not_transform(self):
        # the move path writes Occurrence.transform2 - the property assembly_get's read path
        # prefers - not the legacy Occurrence.transform, which must stay untouched by the move.
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        original_transform = o.transform
        _payload(asm.move_handler(occurrence="Block:1", dx=10, units="mm"))
        assert o.transform2 is not original_transform
        assert o.transform is original_transform

    def test_translation_scaled_to_cm(self):
        _, occs, _ = _install(["Block:1"])
        _payload(asm.move_handler(occurrence="Block:1", dx=10, units="mm"))
        o = _occ(occs, "Block:1")
        # the Vector3D used for translation should be in cm (10mm -> 1cm)
        vec = o.transform2.translation
        assert vec is not None and abs(vec[1] - 1.0) < 1e-9

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.move_handler(occurrence="Ghost", dx=5)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_zero_move_errors(self):
        _install(["Block:1"])
        res = asm.move_handler(occurrence="Block:1")
        assert res["isError"] is True and "no movement" in res["message"].lower()

    def test_rotate_world_axis(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_deg=90, rotate_axis="y"))
        o = _occ(occs, "Block:1")
        # setToRotation takes RADIANS: 90 deg in must reach the API as pi/2, about world Y
        angle, axis, _origin = o.transform2.rotation
        assert angle == pytest.approx(math.radians(90))
        assert axis == ("vec", 0, 1, 0)
        assert out["rotate_axis"] == "y"

    def test_multi_axis_rotation(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_x=90, rotate_z=45))
        o = _occ(occs, "Block:1")
        # The handler builds ONE working matrix (per-axis rotations composed onto it), then composes
        # that onto the occurrence's original transform2 - so o.transform2.composed holds the working
        # matrix, and the per-axis rotations are ITS composed list: X then Z, each angle in RADIANS.
        assert len(o.transform2.composed) == 1
        working = o.transform2.composed[0]
        rots = [m.rotation for m in working.composed if getattr(m, "rotation", None) is not None]
        assert [(r[0], r[1]) for r in rots] == [
            (pytest.approx(math.radians(90)), ("vec", 1, 0, 0)),
            (pytest.approx(math.radians(45)), ("vec", 0, 0, 1)),
        ]
        assert out["rotate_axis"] == "multi"
        assert out["rotate_xyz"] == {"x": 90, "y": 0, "z": 45}

    def test_single_and_multi_rejected_together(self):
        _install(["Block:1"])
        res = asm.move_handler(occurrence="Block:1", rotate_deg=30, rotate_x=10)
        assert res["isError"] is True and "not both" in res["message"]

    # -- jointed-occurrence warning (posing a jointed part is allowed but transient) --
    # Moving a jointed occurrence poses it along its DOF (the sanctioned path - joint_edit redirects
    # here), but the pose is transient and a move that fights the joints over-constrains the solve. So
    # the move PROCEEDS and warns to capture_position + probe.
    # (NOT a refusal: refusing would dead-end the only safe pose path, since driving
    # jointMotion.rotationValue crashes the connection - see joint_create_edit.py.)

    def test_move_jointed_occurrence_proceeds_with_warning(self):
        _, occs, _ = _install(["Block:1"])
        _occ(occs, "Block:1").joints = _FakeJointColl(["Flywheel_Spin", "Rigid3"])
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_deg=180, rotate_axis="x"))
        # it MOVED (pose path is allowed)
        assert out["moved"] is True
        assert _occ(occs, "Block:1").transform2.rotation is not None
        # and it WARNED, naming the joints + the capture/health-check next step
        assert "Flywheel_Spin" in out["jointed_joints"]
        assert "capture_position" in out["jointed_warning"]
        assert "assembly_get" in out["jointed_warning"]

    def test_quiet_suppresses_the_jointed_warning(self):
        _, occs, _ = _install(["Block:1"])
        _occ(occs, "Block:1").joints = _FakeJointColl(["Flywheel_Spin"])
        out = _payload(asm.move_handler(occurrence="Block:1", dx=5, quiet=True))
        assert out["moved"] is True
        assert "jointed_warning" not in out

    def test_unjointed_move_has_no_warning(self):
        _, occs, _ = _install(["Block:1"])  # default: no joints
        out = _payload(asm.move_handler(occurrence="Block:1", dx=5))
        assert out["moved"] is True
        assert "jointed_warning" not in out

    def test_rotate_about_edge_handle(self):
        # AxisRef edge path: rotate about a straight EDGE's line (hinge), not the occ origin.
        design, occs, _ = _install(["Block:1"])
        import adsk.fusion
        class _Edge:
            # LIVE shape: a bounded edge's geometry is a Line3D - startPoint/endPoint ONLY
            # (no .direction/.origin; those are InfiniteLine3D's) - the direction is DERIVED.
            geometry = _line3d((5, 0, 0), (9, 0, 0))
        adsk.fusion.BRepEdge = _Edge
        edge = _Edge()
        h = "/v" + "E" * 70
        # the handler now resolves its design via _common.design() (same seam _inputs uses), so the ONE
        # design must BOTH list the occurrences AND resolve the edge handle: extend the real design.
        real = asm.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_deg=45, rotate_axis=h))
        o = _occ(occs, "Block:1")
        # rotation set about the edge's derived unit direction + a point ON the edge (5,0,0),
        # not the occ origin; the 45 deg input reaches setToRotation in RADIANS
        angle, axis, origin = o.transform2.rotation
        assert angle == pytest.approx(math.radians(45))
        assert (axis.x, axis.y, axis.z) == (1.0, 0.0, 0.0)
        assert (origin.x, origin.y, origin.z) == (5, 0, 0)
        assert out["rotate_axis"] == "edge"

    def test_rotate_about_construction_axis_infinite_line(self):
        # An InfiniteLine3D-shaped geometry (a construction axis) carries origin/direction
        # directly - the fallback read path, no derivation.
        design, occs, _ = _install(["Block:1"])
        import adsk.fusion, adsk.core
        class _InfGeom:
            curveType = adsk.core.Curve3DTypes.Line3DCurveType
            direction = _Vec(0, 0, 1)
            origin = _Pt(2, 2, 0)
        class _Edge:
            geometry = _InfGeom()
        adsk.fusion.BRepEdge = _Edge
        edge = _Edge()
        h = "/v" + "E" * 70
        real = asm.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_deg=30, rotate_axis=h))
        angle, axis, origin = _occ(occs, "Block:1").transform2.rotation
        assert angle == pytest.approx(math.radians(30))
        assert (axis.x, axis.y, axis.z) == (0, 0, 1)
        assert (origin.x, origin.y, origin.z) == (2, 2, 0)
        assert out["rotate_axis"] == "edge"

    def test_unreadable_edge_geometry_errors(self):
        # An edge whose line geometry exposes neither start/end points nor origin/direction
        # must error, never rotate about a guessed axis.
        design, occs, _ = _install(["Block:1"])
        import adsk.fusion, adsk.core
        class _BareGeom:
            curveType = adsk.core.Curve3DTypes.Line3DCurveType
        class _Edge:
            geometry = _BareGeom()
        adsk.fusion.BRepEdge = _Edge
        edge = _Edge()
        h = "/v" + "E" * 70
        real = asm.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        res = asm.move_handler(occurrence="Block:1", rotate_deg=45, rotate_axis=h)
        assert res["isError"] is True
        assert "line geometry" in res["message"]

    def test_combined_rotate_and_translate_preserves_pivot(self):
        # A SINGLE call doing rotation-about-a-pivot AND translation must not assign
        # `mat.translation = vec` on the rotation matrix (that overwrites the pivot column, so the part
        # rotates about the WORLD origin). The translation is composed as its OWN matrix. Use the
        # edge-rotate path (a real non-origin pivot at (5,0,0)) + a translation in the same call.
        import adsk.core, adsk.fusion
        class _Edge:
            geometry = _line3d((5, 0, 0), (9, 0, 0))
        adsk.fusion.BRepEdge = _Edge
        edge = _Edge()
        h = "/v" + "E" * 70
        _, occs, _ = _install(["Block:1"])
        # capture every Matrix3D created - AFTER _install (which re-binds Matrix3D.create to FakeMatrix).
        created = []
        adsk.core.Matrix3D.create = staticmethod(lambda: created.append(FakeMatrix()) or created[-1])
        real = asm.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.move_handler(occurrence="Block:1", rotate_deg=90, rotate_axis=h, dx=10))
        assert out["moved"] is True
        # the matrix that holds the rotation must NOT have had .translation assigned directly.
        rot_mats = [m for m in created if m.rotation is not None]
        assert rot_mats, "expected a rotation matrix to be created"
        for m in rot_mats:
            assert m.direct_translation_assigned_after_rotation is False, (
                "translation was assigned directly onto the rotation matrix - clobbers the pivot column")
        # and a translation was applied by COMPOSITION (transformBy), not assignment.
        assert any(getattr(c, "translation", None) is not None
                   for m in created for c in m.composed), "translation should be composed via transformBy"


# -- assembly_rigid_group ----------------------------------------------------------

class TestRigidGroup:
    def test_group_reporting_fewer_members_bites(self):
        # the group was created but reads fewer members than were requested -> error, not ok
        _, occs, rg = _install(["A:1", "B:1"])

        class ThinGroup:
            name = "RigidGroup1"
            occurrences = type("C", (), {"count": 1})()

        rg.add = lambda coll, inc: ThinGroup()
        res = asm.rigid_group_handler(occurrences="A:1, B:1")
        assert res["isError"] is True
        assert "1 member(s)" in res["message"]

    def test_groups_named_occurrences(self):
        _, occs, rg = _install(["A:1", "B:1", "C:1"])
        out = _payload(asm.rigid_group_handler(occurrences="A:1, B:1"))
        coll, include = rg.last
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "B:1"]

    def test_include_children_flag(self):
        _, occs, rg = _install(["A:1", "B:1"])
        asm.rigid_group_handler(occurrences="A:1, B:1", include_children=True)
        _, include = rg.last
        assert include is True

    def test_needs_at_least_two(self):
        _install(["A:1"])
        res = asm.rigid_group_handler(occurrences="A:1")
        assert res["isError"] is True and "at least two" in res["message"].lower()

    def test_missing_reported(self):
        _install(["A:1"])
        res = asm.rigid_group_handler(occurrences="A:1, Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_accepts_a_list_not_just_comma_string(self):
        # _resolve_many handles both a comma string and an actual list of names.
        _, occs, rg = _install(["A:1", "B:1", "C:1"])
        out = _payload(asm.rigid_group_handler(occurrences=["A:1", "C:1"]))
        coll, _ = rg.last
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "C:1"]

    def test_list_with_blank_entries_filtered(self):
        # empty/whitespace entries are dropped before resolution.
        _, occs, rg = _install(["A:1", "B:1"])
        out = _payload(asm.rigid_group_handler(occurrences=["A:1", "  ", "B:1"]))
        assert out["grouped"] == ["A:1", "B:1"]


class TestMoveNote:
    def test_jointed_move_note_differs_from_free_move(self):
        # the result 'note' reflects whether the part was posed (jointed) vs a plain free move.
        _, occs, _ = _install(["Block:1"])
        free = _payload(asm.move_handler(occurrence="Block:1", dx=5))
        assert "free move" in free["note"]
        _occ(occs, "Block:1").joints = _FakeJointColl(["Spin1"])
        posed = _payload(asm.move_handler(occurrence="Block:1", dx=5))
        assert "jointed" in posed["note"].lower()
