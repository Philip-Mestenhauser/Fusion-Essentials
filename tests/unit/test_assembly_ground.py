"""Unit tests for ``assembly_ground.py`` - the isGroundToParent parent lock.

The logic pinned here, no live Fusion: occurrence resolution through the shared ambiguity-refusing
resolver, the flag's read-back (a stuck flag is an error, never a reported lock), and the
position_reset the lock's snap-back is disclosed with. Fakes expose the real attributes the handler
sets so we can assert on them.
"""

import json

import pytest

from conftest import load_tool, _make_object_collection


asm = load_tool("assembly_ground")


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


class TestGround:
    # assembly_ground sets ONLY isGroundToParent (the stateless parent lock). The UI Ground/Fix
    # flag (isGrounded) is deliberately not settable - the platform treats it as legacy - but
    # both flags are reported back so the caller sees the full grounding state.
    def test_lock_to_parent(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=True))
        assert _occ(occs, "Block:1").isGroundToParent is True
        assert out["isGroundToParent"] is True

    def test_unground_from_parent_releases_lock(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=False))
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
        res = asm.handler(occurrence="Block:1", ground_to_parent=False)
        assert res["isError"] is True
        assert "did not take" in res["message"]

    def test_unreadable_flag_after_the_set_is_unconfirmed_not_ok(self):
        # the confirming read RAISES: a flag that cannot be read is not a confirmation, so a
        # swallowed write must not pass as a set lock. Unconfirmed is an error, never an ok.
        _, occs, _ = _install(["Block:1"])

        class BlindOcc(FakeOcc):
            @property
            def isGroundToParent(self):
                raise RuntimeError("isGroundToParent unreadable")

            @isGroundToParent.setter
            def isGroundToParent(self, v):
                pass

        occs[0].__class__ = BlindOcc
        res = asm.handler(occurrence="Block:1", ground_to_parent=False)
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"]
        assert "cannot be read" in res["message"]

    def test_unreadable_isGrounded_reports_null_not_false(self):
        # the read-only context flag is published as null when it cannot be read - False is an
        # answer ("not fixed in the UI") and would be a reading nobody took.
        _, occs, _ = _install(["Block:1"])

        class NoUiFlagOcc(FakeOcc):
            @property
            def isGrounded(self):
                raise RuntimeError("isGrounded unreadable")

        occs[0].__class__ = NoUiFlagOcc
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=True))
        assert out["isGrounded"] is None
        assert out["isGroundToParent"] is True

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
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=True))
        assert out["position_reset"] == {"from_mm": [20.0, 0.0, 0.0], "to_mm": [0.0, 0.0, 0.0]}
        assert "SNAPPED" in out["position_warning"]

    def test_no_snap_reports_no_reset(self):
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        o.transform._translation = _Vec(2.0, 0.0, 0.0)       # position held through the flag set
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=True))
        assert "position_reset" not in out and "position_warning" not in out

    def test_isGrounded_is_never_written(self):
        # The tool sets ONLY isGroundToParent; the UI Ground/Fix flag (isGrounded) is not settable
        # through this surface and stays exactly as it was.
        _, occs, _ = _install(["Block:1"])
        o = _occ(occs, "Block:1")
        o.isGrounded = False
        asm.handler(occurrence="Block:1", ground_to_parent=True)
        assert o.isGrounded is False              # untouched

    def test_no_grounded_param_is_rejected_by_strict_schema(self):
        # There is no 'grounded' input - only 'ground_to_parent'. (At the MCP boundary the strict
        # schema rejects it; at the Python level it's a TypeError.)
        _install(["Block:1"])
        import pytest
        with pytest.raises(TypeError):
            asm.handler(occurrence="Block:1", grounded=True)

    def test_mode_input_is_gone(self):
        # There is no 'mode' selector - isGrounded is not reachable through any input. (Strict
        # schema at the MCP boundary; TypeError at the Python level.)
        _install(["Block:1"])
        import pytest
        with pytest.raises(TypeError):
            asm.handler(occurrence="Block:1", ground_to_parent=True, mode="fixed")

    def test_reports_both_flags_distinctly(self):
        # the payload carries BOTH flags so the caller sees the full grounding state (a human may
        # have set isGrounded in the UI).
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="Block:1", ground_to_parent=True))
        assert "isGroundToParent" in out and "isGrounded" in out
        assert out["isGroundToParent"] is True

    def test_substring_match(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.handler(occurrence="block", ground_to_parent=True))
        assert out["occurrence"] == "Block:1"

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.handler(occurrence="Ghost", ground_to_parent=True)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_no_change_requested_errors(self):
        _install(["Block:1"])
        res = asm.handler(occurrence="Block:1")
        assert res["isError"] is True and "ground_to_parent" in res["message"]

    def test_ambiguous_name_refused_not_wrong_instance(self):
        # The wrong-instance bug: two instances share the local name "Bolt:1" under different
        # sub-assemblies. A bare "Bolt" substring must ERROR (naming both fullPathNames), NOT silently
        # ground the first one.
        _, occs, _ = _install([])
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1"); a.isGroundToParent = False
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1"); b.isGroundToParent = False
        asm._common.app.activeProduct.rootComponent.allOccurrences = [a, b]
        res = asm.handler(occurrence="Bolt", ground_to_parent=True)
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        # And neither was mutated (the ambiguous call refused before touching either).
        assert a.isGroundToParent is False and b.isGroundToParent is False

    def test_exact_full_path_targets_the_right_instance(self):
        _, occs, _ = _install([])
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1"); a.isGroundToParent = False
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1"); b.isGroundToParent = False
        asm._common.app.activeProduct.rootComponent.allOccurrences = [a, b]
        res = asm.handler(occurrence="Sub-B:1+Bolt:1", ground_to_parent=True)
        assert res["isError"] is False
        assert b.isGroundToParent is True and a.isGroundToParent is False   # the RIGHT one


# -- assembly_move ---------------------------------------------------------------
