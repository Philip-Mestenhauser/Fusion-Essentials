"""Unit tests for ``assembly_move.py`` - the free reposition.

The logic pinned here, no live Fusion: assembly_move building a Matrix3D translation/rotation and
applying it to occurrence.transform2 (the same property assembly_get's own read path prefers), the
unchanged-transform refusal, and the jointed_warning. Fakes expose the real attributes the handler
sets so we can assert on them.
"""

import json
import math

import pytest

from conftest import load_tool, _make_object_collection


asm = load_tool("assembly_move")


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
        """Compose `other` onto this matrix. A real Matrix3D ACCUMULATES - two successive moves of
        +5mm leave the occurrence 10mm out - so the rotation angle and the translation add rather
        than replace. Overwriting them would make a second identical move read as an unchanged
        transform, i.e. as no move at all."""
        self.composed.append(other)
        if getattr(other, "rotation", None) is not None:
            angle, axis, origin = other.rotation
            prior = self.rotation[0] if self.rotation is not None else 0.0
            self.rotation = (prior + angle, axis, origin)
        ot = getattr(other, "translation", None)
        if ot is not None:
            if self._translation is None:
                self._translation = ot
            else:
                a, b = self._xyz(self._translation), self._xyz(ot)
                self._translation = ("vec", a[0] + b[0], a[1] + b[1], a[2] + b[2])

    @staticmethod
    def _xyz(v):
        """The three components of whatever stands in for a Vector3D here - the ('vec', x, y, z)
        tuple Vector3D.create is faked as, or a _Vec/Point3D-shaped object."""
        if v is None:
            return (0.0, 0.0, 0.0)
        if isinstance(v, tuple):
            return tuple(float(c) for c in v[1:4])
        return (float(getattr(v, "x", 0.0)), float(getattr(v, "y", 0.0)),
                float(getattr(v, "z", 0.0)))

    def asArray(self):
        """Matrix3D.asArray - the 16 row-major floats. The move handler reads this on BOTH sides of
        the write and refuses when either read is unavailable, so a fake without it sends every move
        down the UNCONFIRMED path. State-sensitive by construction: the translation column and the
        rotation angle come off this matrix's current values, so a matrix that was composed reads
        differently from one that was not (which is exactly what the before/after compare asks)."""
        tx, ty, tz = self._xyz(self._translation)
        angle = float(self.rotation[0]) if self.rotation is not None else 0.0
        return (1.0, 0.0, 0.0, tx,
                0.0, 1.0, 0.0, ty,
                0.0, 0.0, 1.0, tz,
                0.0, 0.0, angle, 1.0)


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
        # A real Occurrence always answers `component`; a read that RAISES is the
        # unresolved-external-reference signal the shared occurrence census filters on.
        self.component = type("C", (), {"name": name.split(":")[0]})()

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
    asm._common.app = type("A", (), {"activeProduct": design})()
    
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ObjectCollection.create = staticmethod(_make_object_collection)
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



class TestMove:
    def test_move_that_does_not_take_bites(self):
        # the transform assignment is accepted but the pose reads unchanged -> error, not ok
        _, occs, _ = _install(["Block:1"])

        class FrozenMatrix(FakeMatrix):
            def asArray(self):
                return (1.0,) * 16          # constant pose: the assignment never takes

        occs[0].transform2 = FrozenMatrix()
        res = asm.handler(occurrence="Block:1", dx=10)
        assert res["isError"] is True
        assert "did not move" in res["message"]

    # An UNREADABLE transform is not a confirmation. The compare that proves the move took needs a
    # reading on BOTH sides, so a missing reading on either one is a refusal - publishing moved:true
    # with a null position beside it would assert an effect no read took. Each side is pinned
    # separately so a guard covering only one of them still goes red.
    def test_an_unreadable_pose_AFTER_the_write_is_refused_not_moved_true(self):
        _, occs, _ = _install(["Block:1"])

        class BlindAfter(FakeMatrix):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def asArray(self):
                self.reads += 1
                if self.reads > 1:          # the BEFORE read answers, the AFTER read does not
                    raise RuntimeError("transform unreadable")
                return super().asArray()

        occs[0].transform2 = BlindAfter()
        res = asm.handler(occurrence="Block:1", dx=10)
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"]
        assert "after the change" in res["message"]

    def test_an_unreadable_pose_BEFORE_the_write_is_refused_not_moved_true(self):
        _, occs, _ = _install(["Block:1"])

        class BlindBefore(FakeMatrix):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def asArray(self):
                self.reads += 1
                if self.reads == 1:         # only the BEFORE read fails
                    raise RuntimeError("transform unreadable")
                return super().asArray()

        occs[0].transform2 = BlindBefore()
        res = asm.handler(occurrence="Block:1", dx=10)
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"]
        assert "before the change" in res["message"]

    def test_a_wholly_unreadable_pose_names_both_sides(self):
        _, occs, _ = _install(["Block:1"])

        class Blind(FakeMatrix):
            def asArray(self):
                raise RuntimeError("transform unreadable")

        occs[0].transform2 = Blind()
        res = asm.handler(occurrence="Block:1", dx=10)
        assert res["isError"] is True
        assert "before and after the change" in res["message"]

    def test_a_readable_pose_on_both_sides_still_moves(self):
        # the refusal is scoped to the unreadable case - a normal move is untouched by it
        _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", dx=10))
        assert out["moved"] is True
        assert "UNCONFIRMED" not in out.get("note", "")

    def test_translate_sets_transform(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", dx=10, dy=0, dz=5, units="mm"))
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
        _payload(asm.handler(occurrence="Block:1", dx=10, units="mm"))
        assert o.transform2 is not original_transform
        assert o.transform is original_transform

    def test_translation_scaled_to_cm(self):
        _, occs, _ = _install(["Block:1"])
        _payload(asm.handler(occurrence="Block:1", dx=10, units="mm"))
        o = _occ(occs, "Block:1")
        # the Vector3D used for translation should be in cm (10mm -> 1cm)
        vec = o.transform2.translation
        assert vec is not None and abs(vec[1] - 1.0) < 1e-9

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.handler(occurrence="Ghost", dx=5)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_zero_move_errors(self):
        _install(["Block:1"])
        res = asm.handler(occurrence="Block:1")
        assert res["isError"] is True and "no movement" in res["message"].lower()

    def test_rotate_world_axis(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", rotate_deg=90, rotate_axis="y"))
        o = _occ(occs, "Block:1")
        # setToRotation takes RADIANS: 90 deg in must reach the API as pi/2, about world Y
        angle, axis, _origin = o.transform2.rotation
        assert angle == pytest.approx(math.radians(90))
        assert axis == ("vec", 0, 1, 0)
        assert out["rotate_axis"] == "y"

    def test_multi_axis_rotation(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", rotate_x=90, rotate_z=45))
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
        res = asm.handler(occurrence="Block:1", rotate_deg=30, rotate_x=10)
        assert res["isError"] is True and "not both" in res["message"]

    # -- jointed-occurrence warning (posing a jointed part is allowed but transient) --
    # Moving a jointed occurrence poses it along its DOF (the sanctioned path - joint_edit redirects
    # here), but the pose is transient and a move that fights the joints over-constrains the solve. So
    # the move PROCEEDS and warns to capture_position + probe.
    # (NOT a refusal: refusing would dead-end the only safe pose path, since driving
    # jointMotion.rotationValue crashes the connection - see joint_edit.py.)

    def test_move_jointed_occurrence_proceeds_with_warning(self):
        _, occs, _ = _install(["Block:1"])
        _occ(occs, "Block:1").joints = _FakeJointColl(["Flywheel_Spin", "Rigid3"])
        out = _payload(asm.handler(occurrence="Block:1", rotate_deg=180, rotate_axis="x"))
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
        out = _payload(asm.handler(occurrence="Block:1", dx=5, quiet=True))
        assert out["moved"] is True
        assert "jointed_warning" not in out

    def test_unjointed_move_has_no_warning(self):
        _, occs, _ = _install(["Block:1"])  # default: no joints
        out = _payload(asm.handler(occurrence="Block:1", dx=5))
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
        real = asm._common.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.handler(occurrence="Block:1", rotate_deg=45, rotate_axis=h))
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
        real = asm._common.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.handler(occurrence="Block:1", rotate_deg=30, rotate_axis=h))
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
        real = asm._common.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        res = asm.handler(occurrence="Block:1", rotate_deg=45, rotate_axis=h)
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
        real = asm._common.app.activeProduct
        real.findEntityByToken = lambda t, e=edge, hh=h: ([e] if t == hh else [])
        asm._common.design = lambda: real
        asm._inputs._common.design = lambda: real
        out = _payload(asm.handler(occurrence="Block:1", rotate_deg=90, rotate_axis=h, dx=10))
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



class TestMoveNote:
    def test_jointed_move_note_differs_from_free_move(self):
        # the result 'note' reflects whether the part was posed (jointed) vs a plain free move.
        _, occs, _ = _install(["Block:1"])
        free = _payload(asm.handler(occurrence="Block:1", dx=5))
        assert "free move" in free["note"]
        _occ(occs, "Block:1").joints = _FakeJointColl(["Spin1"])
        posed = _payload(asm.handler(occurrence="Block:1", dx=5))
        assert "jointed" in posed["note"].lower()
