"""Unit tests for ``assembly.py`` — occurrence ground/move + rigid group.

Tests are written BEFORE the tool is wired further (project rule). The logic
pinned here, no live Fusion: occurrence resolution (exact, then substring;
missing reported), the TWO distinct ground flags (isGrounded = pin-in-space vs.
isGroundToParent = release-from-parent-lock), move_occurrence building a
Matrix3D translation/rotation and applying it to occurrence.transform, and
rigid_group collecting occurrences into RigidGroups.add. Fakes expose the real
attributes the handlers set so we can assert on them.
"""

import json

from conftest import load_tool

asm = load_tool("assembly")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeVec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def asPoint(self):
        return ("pt", self.x, self.y, self.z)


class FakeMatrix:
    """Models the subset of Matrix3D the move handler touches: a settable 'translation'
    (Vector3D), transformBy (compose), and setToRotation."""
    def __init__(self):
        self.translation = None        # set to a ('vec', x, y, z) by the handler
        self.rotation = None

    def setToRotation(self, angle, axis, origin):
        self.rotation = (angle, axis, origin)

    def transformBy(self, other):
        # composing: carry the other matrix's translation onto this base
        if getattr(other, "translation", None) is not None:
            self.translation = other.translation
        if getattr(other, "rotation", None) is not None:
            self.rotation = other.rotation


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name
        self.isGrounded = False
        self.isGroundToParent = True
        self.transform = FakeMatrix()
        self.transform_applied = None

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
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ObjectCollection.create = staticmethod(FakeObjectCollection)
    # Matrix3D + Vector3D for move
    adsk.core.Matrix3D.create = staticmethod(FakeMatrix)
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    return design, occs, rg


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _occ(occs, name):
    return next(o for o in occs if o.name == name)


# ── ground ───────────────────────────────────────────────────────────────────

class TestGround:
    def test_ground_pins_in_space(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="Block:1", grounded=True))
        assert _occ(occs, "Block:1").isGrounded is True
        assert out["isGrounded"] is True

    def test_unground_from_parent_releases_lock(self):
        # The nuance: isGroundToParent is the default rigid-to-parent lock.
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="Block:1", ground_to_parent=False))
        assert _occ(occs, "Block:1").isGroundToParent is False
        assert out["isGroundToParent"] is False

    def test_both_flags_independent(self):
        _, occs, _ = _install(["Block:1"])
        asm.ground_handler(occurrence="Block:1", grounded=True, ground_to_parent=False)
        o = _occ(occs, "Block:1")
        assert o.isGrounded is True and o.isGroundToParent is False

    def test_substring_match(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.ground_handler(occurrence="block", grounded=True))
        assert out["occurrence"] == "Block:1"

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.ground_handler(occurrence="Ghost", grounded=True)
        assert res["isError"] is True and "No occurrence matched" in res["message"]

    def test_no_change_requested_errors(self):
        _install(["Block:1"])
        res = asm.ground_handler(occurrence="Block:1")
        assert res["isError"] is True and "Specify" in res["message"]


# ── move_occurrence ──────────────────────────────────────────────────────────

class TestMove:
    def test_translate_sets_transform(self):
        _, occs, _ = _install(["Block:1"])
        out = _payload(asm.move_handler(occurrence="Block:1", dx=10, dy=0, dz=5, units="mm"))
        o = _occ(occs, "Block:1")
        # a new transform matrix was assigned
        assert o.transform is not None
        assert out["moved"] is True
        assert out["translation_mm"] == {"x": 10, "y": 0, "z": 5}

    def test_translation_scaled_to_cm(self):
        _, occs, _ = _install(["Block:1"])
        _payload(asm.move_handler(occurrence="Block:1", dx=10, units="mm"))
        o = _occ(occs, "Block:1")
        # the Vector3D used for translation should be in cm (10mm -> 1cm)
        vec = o.transform.translation
        assert vec is not None and abs(vec[1] - 1.0) < 1e-9

    def test_missing_occurrence_errors(self):
        _install(["Block:1"])
        res = asm.move_handler(occurrence="Ghost", dx=5)
        assert res["isError"] is True and "No occurrence matched" in res["message"]

    def test_zero_move_errors(self):
        _install(["Block:1"])
        res = asm.move_handler(occurrence="Block:1")
        assert res["isError"] is True and "no movement" in res["message"].lower()


# ── rigid_group ──────────────────────────────────────────────────────────────

class TestRigidGroup:
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
