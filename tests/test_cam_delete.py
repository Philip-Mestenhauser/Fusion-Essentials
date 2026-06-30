"""Unit tests for ``cam_delete`` — delete any CAM entity (setup / operation / folder / pattern).

The adsk.cam API is mocked; what we pin is the tool's OWN logic: finding the named entity across all
setups (setups themselves by name, and operations/folders/patterns via each setup's allOperations),
calling .deleteMe(), turning a deleteMe()==False into an explicit error (never a false success), and
the guards (no CAM, nothing named that, ambiguous name across the doc). This fills the gap that
design_delete_feature/_occurrence (timeline-only) do NOT cover CAM entities.
"""

import json

from conftest import load_tool

cd = load_tool("cam_delete")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Entity:
    def __init__(self, name, kind, can_delete=True):
        self.name = name
        self._kind = kind            # for the reported entity_type via type name
        self._can = can_delete
        self.deleted = False
    def deleteMe(self):
        if self._can:
            self.deleted = True
        return self._can


# distinct classes so the tool can report a sensible entity_type from type(x).__name__

class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class _Container:
    """A setup/folder/pattern: has operations / folders / patterns collections. CRITICAL: allOperations
    returns ONLY operations (NOT folders/patterns) — modelling the live behavior the tool must work around."""
    def __init__(self, ops=(), folders=(), patterns=()):
        self.operations = _Coll(list(ops))
        self.folders = _Coll(list(folders))
        self.patterns = _Coll(list(patterns))
    @property
    def allOperations(self):
        # flattens nested operations, but DROPS folders/patterns (the real API gap)
        flat = list(self.operations._i)
        for f in self.folders._i:
            flat.extend(getattr(f, "operations", _Coll([]))._i)
        return _Coll(flat)


class Setup(_Entity, _Container):
    def __init__(self, name, ops=(), folders=(), patterns=(), can_delete=True):
        _Entity.__init__(self, name, "setup", can_delete)
        _Container.__init__(self, ops, folders, patterns)


class Operation(_Entity):
    def __init__(self, name, can_delete=True):
        super().__init__(name, "operation", can_delete)


class CAMFolder(_Entity, _Container):
    def __init__(self, name, ops=(), folders=(), patterns=(), can_delete=True):
        _Entity.__init__(self, name, "folder", can_delete)
        _Container.__init__(self, ops, folders, patterns)


class CAMPattern(_Entity, _Container):
    def __init__(self, name, ops=(), folders=(), patterns=(), can_delete=True):
        _Entity.__init__(self, name, "pattern", can_delete)
        _Container.__init__(self, ops, folders, patterns)


class _Setups:
    def __init__(self, setups):
        self._s = setups
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
        # a folder with a nested op + a nested pattern, plus two loose ops — folder/pattern are NOT in
        # allOperations, so the tool must walk .folders/.patterns to reach them.
        nested_op = Operation("Drill1")
        pat = CAMPattern("Pattern1", ops=[Operation("Bore1")])
        fol = CAMFolder("Holes", ops=[nested_op], patterns=[pat])
        s = Setup("Setup1", ops=[Operation("Face1"), Operation("Adaptive1")], folders=[fol])
        setups = [s]
    cam = _CAM(setups)
    cd._get_cam = lambda: (cam, None)
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self):
        cd._get_cam = lambda: (None, "no CAM data")
        res = cd.handler(entity="Face1")
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_requires_entity(self):
        _install()
        res = cd.handler(entity="")
        assert res["isError"] is True

    def test_not_found(self):
        _install()
        res = cd.handler(entity="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_ambiguous_name(self):
        # two operations named the same across setups -> refuse rather than guess
        s1 = Setup("S1", ops=[Operation("Dup")])
        s2 = Setup("S2", ops=[Operation("Dup")])
        _install([s1, s2])
        res = cd.handler(entity="Dup")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()


# ── delete ───────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_operation(self):
        cam = _install()
        out = _payload(cd.handler(entity="Face1"))
        op = cam.setups.item(0).allOperations.item(0)
        assert op.deleted is True
        assert out["deleted"] is True and out["entity"] == "Face1"

    def test_delete_folder(self):
        cam = _install()
        out = _payload(cd.handler(entity="Holes"))
        assert out["deleted"] is True
        assert out["entity_type"] == "folder"

    def test_delete_nested_pattern(self):
        # a pattern lives inside a folder and is NOT in allOperations — the tool must walk the tree
        # (.folders -> .patterns) to find it. This was the live bug.
        cam = _install()
        out = _payload(cd.handler(entity="Pattern1"))
        assert out["deleted"] is True and out["entity_type"] == "pattern"

    def test_delete_op_nested_in_folder(self):
        cam = _install()
        out = _payload(cd.handler(entity="Drill1"))
        assert out["deleted"] is True and out["entity_type"] == "operation"

    def test_delete_setup(self):
        cam = _install()
        out = _payload(cd.handler(entity="Setup1"))
        assert cam.setups.item(0).deleted is True
        assert out["entity_type"] == "setup"

    def test_deleteme_false_is_error(self):
        # Fusion declining the delete (deleteMe()==False) must be a hard error, not a false ok
        s = Setup("Setup1", ops=[Operation("Stubborn", can_delete=False)])
        _install([s])
        res = cd.handler(entity="Stubborn")
        assert res["isError"] is True and "declin" in res["message"].lower()
