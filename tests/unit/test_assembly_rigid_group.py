"""Unit tests for ``assembly_rigid_group.py`` - collecting occurrences into RigidGroups.add.

The logic pinned here, no live Fusion: the at-least-two guard, the resolve of every named
occurrence through the shared ambiguity-refusing resolver, and the member-count read-back.
"""

import json


from conftest import load_tool, _make_object_collection


asm = load_tool("assembly_rigid_group")


# -- fakes ---------------------------------------------------------------------


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


# -- ground ---------------------------------------------------------------------


class TestRigidGroup:
    def test_group_reporting_fewer_members_bites(self):
        # the group was created but reads fewer members than were requested -> error, not ok
        _, occs, rg = _install(["A:1", "B:1"])

        class ThinGroup:
            name = "RigidGroup1"
            occurrences = type("C", (), {"count": 1})()

        rg.add = lambda coll, inc: ThinGroup()
        res = asm.handler(occurrences="A:1, B:1")
        assert res["isError"] is True
        assert "1 member(s)" in res["message"]

    def test_groups_named_occurrences(self):
        _, occs, rg = _install(["A:1", "B:1", "C:1"])
        out = _payload(asm.handler(occurrences="A:1, B:1"))
        coll, include = rg.last
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "B:1"]

    def test_include_children_flag(self):
        _, occs, rg = _install(["A:1", "B:1"])
        asm.handler(occurrences="A:1, B:1", include_children=True)
        _, include = rg.last
        assert include is True

    def test_needs_at_least_two(self):
        _install(["A:1"])
        res = asm.handler(occurrences="A:1")
        assert res["isError"] is True and "at least two" in res["message"].lower()

    def test_missing_reported(self):
        _install(["A:1"])
        res = asm.handler(occurrences="A:1, Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_accepts_a_list_not_just_comma_string(self):
        # _resolve_many handles both a comma string and an actual list of names.
        _, occs, rg = _install(["A:1", "B:1", "C:1"])
        out = _payload(asm.handler(occurrences=["A:1", "C:1"]))
        coll, _ = rg.last
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "C:1"]

    def test_list_with_blank_entries_filtered(self):
        # empty/whitespace entries are dropped before resolution.
        _, occs, rg = _install(["A:1", "B:1"])
        out = _payload(asm.handler(occurrences=["A:1", "  ", "B:1"]))
        assert out["grouped"] == ["A:1", "B:1"]
