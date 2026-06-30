"""Unit tests for ``cam_reorder`` — reorder a CAM operation/folder/pattern before or after another.

The adsk.cam API is mocked; what we pin is the tool's OWN logic: resolving BOTH the moving entity and
the reference entity by name across the setups (operations + folders + patterns, walked recursively —
since allOperations omits folders/patterns), calling OperationBase.moveBefore / moveAfter, turning a
False return into an error, and the guards (no CAM, position not before/after, entity/reference missing,
moving an entity relative to itself).
"""

import json

from conftest import load_tool

cr = load_tool("cam_reorder")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Entity:
    def __init__(self, name, allow=True):
        self.name = name
        self._allow = allow
        self.moved = None    # ('before'|'after', other)
    def moveBefore(self, other):
        if self._allow:
            self.moved = ("before", other)
        return self._allow
    def moveAfter(self, other):
        if self._allow:
            self.moved = ("after", other)
        return self._allow


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class _Container(_Entity):
    def __init__(self, name, ops=(), folders=(), patterns=(), allow=True):
        super().__init__(name, allow)
        self.operations = _Coll(ops)
        self.folders = _Coll(folders)
        self.patterns = _Coll(patterns)


class Operation(_Entity):
    pass


class Setup(_Container):
    pass


class CAMFolder(_Container):
    pass


class _Setups:
    def __init__(self, s):
        self._s = s
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _CAM:
    def __init__(self, setups):
        self.setups = _Setups(setups)


def _install(setups=None):
    if setups is None:
        ops = [Operation("Face1"), Operation("Adaptive1"), Operation("Drill1")]
        fol = CAMFolder("Holes", ops=[Operation("Bore1")])
        s = Setup("Setup1", ops=ops, folders=[fol])
        setups = [s]
    cam = _CAM(setups)
    cr._get_cam = lambda: (cam, None)
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _named(cam, name):
    # helper to fetch an entity for assertions
    s = cam.setups.item(0)
    for o in s.operations._i:
        if o.name == name:
            return o
    for f in s.folders._i:
        if f.name == name:
            return f
        for o in f.operations._i:
            if o.name == name:
                return o
    return None


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self):
        cr._get_cam = lambda: (None, "no CAM data")
        res = cr.handler(entity="Face1", position="after", reference="Adaptive1")
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_bad_position(self):
        _install()
        res = cr.handler(entity="Face1", position="sideways", reference="Adaptive1")
        assert res["isError"] is True and "position" in res["message"].lower()

    def test_entity_not_found(self):
        _install()
        res = cr.handler(entity="Ghost", position="after", reference="Adaptive1")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_reference_not_found(self):
        _install()
        res = cr.handler(entity="Face1", position="after", reference="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_entity_equals_reference(self):
        _install()
        res = cr.handler(entity="Face1", position="after", reference="Face1")
        assert res["isError"] is True


# ── reorder ──────────────────────────────────────────────────────────────────

class TestReorder:
    def test_move_after(self):
        cam = _install()
        out = _payload(cr.handler(entity="Face1", position="after", reference="Drill1"))
        face = _named(cam, "Face1")
        assert face.moved == ("after", _named(cam, "Drill1"))
        assert out["moved"] == "Face1" and out["position"] == "after" and out["reference"] == "Drill1"

    def test_move_before(self):
        cam = _install()
        cr.handler(entity="Drill1", position="before", reference="Face1")
        assert _named(cam, "Drill1").moved[0] == "before"

    def test_reorder_nested_entity(self):
        # the moving entity can be inside a folder (Bore1) — resolved by the recursive walk
        cam = _install()
        out = _payload(cr.handler(entity="Bore1", position="before", reference="Face1"))
        assert _named(cam, "Bore1").moved[0] == "before"
        assert out["moved"] == "Bore1"

    def test_move_declined_is_error(self):
        # moveBefore/After returning False (an illegal move) must be a hard error, not a false ok
        s = Setup("Setup1", ops=[Operation("A", allow=False), Operation("B")])
        _install([s])
        res = cr.handler(entity="A", position="after", reference="B")
        assert res["isError"] is True and ("not allowed" in res["message"].lower()
                                           or "declin" in res["message"].lower())
