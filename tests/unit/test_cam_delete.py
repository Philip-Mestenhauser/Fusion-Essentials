"""Unit tests for ``cam_delete`` — delete any CAM entity (setup / operation / folder / pattern /
NC program).

The adsk.cam API is mocked; what we pin is the tool's OWN logic: finding the named entity across the
setup tree AND cam.ncPrograms, calling .deleteMe(), judging the delete on the HELD node plus the
name census (never a re-resolve of the address), turning a deleteMe()==False into an explicit error,
and the guards (no CAM, nothing named that, an ambiguous name).
"""

import json

from conftest import FakeCAMFolder, FakeOperation, FakeSetup, _NamedCollection, load_tool, make_cam

cd = load_tool("cam_delete")


# ── the deletable CAM tree ───────────────────────────────────────────────────

class _LiveCollection(_NamedCollection):
    """A CAM collection whose enumeration DROPS deleted entities - the live contract the
    verify-gone re-resolve depends on (a deleted node stops resolving; reading it raises)."""

    @property
    def _items(self):
        return [x for x in self._all if not getattr(x, "deleted", False)]

    @_items.setter
    def _items(self, items):
        self._all = list(items)


class _Regrown(_LiveCollection):
    """A collection that swaps a deleted item for a fresh twin of its name - the name census that
    does NOT fall though the deleted node itself has gone quiet."""

    @property
    def _items(self):
        return [FakeOperation(x._name) if getattr(x, "deleted", False) else x for x in self._all]

    @_items.setter
    def _items(self, items):
        self._all = list(items)


class _Deletable:
    """deleteMe() as a CAM node answers it: the bool the caller gates on, and only a true one takes
    the node out of its parent's enumeration AND makes its own name read raise, as a deleted node's
    same-transaction read does."""

    def __init__(self, *args, can_delete=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._can = can_delete
        self.deleted = False

    @property
    def name(self):
        if getattr(self, "deleted", False):
            raise RuntimeError("the object has been deleted")
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    def deleteMe(self):
        if self._can:
            self.deleted = True
        return self._can


class _Named:
    """A leaf carrying nothing but the name a walk reads off it."""

    def __init__(self, name):
        self.name = name


class _Container(_Deletable):
    """A setup/folder/pattern: the shared container protocol with drop-on-delete child collections."""

    def __init__(self, name, ops=(), folders=(), patterns=(), can_delete=True):
        super().__init__(name, ops=ops, folders=folders, patterns=patterns, can_delete=can_delete)
        self.operations = _LiveCollection(ops)
        self.folders = _LiveCollection(folders)
        self.patterns = _LiveCollection(patterns)


class Operation(_Deletable, FakeOperation):
    pass


class Setup(_Container, FakeSetup):
    pass


class CAMFolder(_Container, FakeCAMFolder):
    pass


class _NCProgram(_Deletable, _Named):
    """An NC program: cam.ncPrograms lists it by name and deleteMe takes it out of that collection.
    NCProgram has no live SHAPES dump, so it keeps a local fake."""


def _install(monkeypatch, setups=None, programs=()):
    if setups is None:
        # a folder with a nested op + a nested pattern, plus two loose ops - the folder and the
        # pattern are NOT in allOperations, so the tool must walk .folders/.patterns to reach them.
        pat = CAMFolder("Pattern1", ops=[Operation("Bore1")])
        fol = CAMFolder("Holes", ops=[Operation("Drill1")], patterns=[pat])
        s = Setup("Setup1", ops=[Operation("Face1"), Operation("Adaptive1")], folders=[fol])
        setups = [s]
    cam = make_cam(*setups)
    cam.setups = _LiveCollection(setups)   # a deleted setup leaves the walk too
    cam.ncPrograms = _LiveCollection(programs)
    monkeypatch.setattr(cd, "get_cam", lambda: (cam, None))
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(cd, "get_cam", lambda: (None, "no CAM data"))
        res = cd.handler(entity="Face1")
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_requires_entity(self, monkeypatch):
        _install(monkeypatch)
        res = cd.handler(entity="")
        assert res["isError"] is True
        assert "Provide 'entity'" in res["message"]

    def test_not_found(self, monkeypatch):
        _install(monkeypatch)
        res = cd.handler(entity="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_ambiguous_name(self, monkeypatch):
        # two operations named the same across setups -> refuse rather than guess, naming each
        # candidate's setup path so the agent can rename/disambiguate.
        s1 = Setup("S1", ops=[Operation("Dup")])
        s2 = Setup("S2", ops=[Operation("Dup")])
        _install(monkeypatch, [s1, s2])
        res = cd.handler(entity="Dup")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "S1 / Dup" in res["message"] and "S2 / Dup" in res["message"]


# ── delete ───────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_operation(self, monkeypatch):
        cam = _install(monkeypatch)
        face1 = cam.setups.item(0).allOperations.item(0)     # held BEFORE - the delete drops it
        out = _payload(cd.handler(entity="Face1"))           # from enumeration (live contract)
        assert face1.deleted is True
        assert out["deleted"] is True and out["entity"] == "Face1"
        assert out["remaining_with_name"] == 0
        assert "'Face1' removed; 0 CAM item(s) still carry that name." in out["note"]

    def test_delete_folder(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cd.handler(entity="Holes"))
        assert out["deleted"] is True
        assert out["entity_type"] == "folder"

    def test_delete_nested_pattern(self, monkeypatch):
        # a pattern lives inside a folder and is NOT in allOperations — the tool must walk the tree
        # (.folders -> .patterns) to find it.
        cam = _install(monkeypatch)
        out = _payload(cd.handler(entity="Pattern1"))
        assert out["deleted"] is True and out["entity_type"] == "pattern"

    def test_delete_op_nested_in_folder(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cd.handler(entity="Drill1"))
        assert out["deleted"] is True and out["entity_type"] == "operation"

    def test_delete_setup(self, monkeypatch):
        cam = _install(monkeypatch)
        setup = cam.setups.item(0)                           # held BEFORE the delete drops it
        out = _payload(cd.handler(entity="Setup1"))
        assert setup.deleted is True
        assert out["entity_type"] == "setup"

    def test_deleteme_false_is_error(self, monkeypatch):
        # Fusion declining the delete (deleteMe()==False) must be a hard error, not a false ok
        s = Setup("Setup1", ops=[Operation("Stubborn", can_delete=False)])
        _install(monkeypatch, [s])
        res = cd.handler(entity="Stubborn")
        assert res["isError"] is True and "declin" in res["message"].lower()

    def test_a_lying_deleteme_true_is_caught_by_the_held_node(self, monkeypatch):
        # deleteMe() returns True and the node is untouched - measured, a genuinely deleted node
        # RAISES on a same-transaction read, so a name that still reads means the platform lied.
        class _Liar(Operation):
            """deleteMe answers true and takes nothing out of the tree."""

            def deleteMe(self):
                return True
        s = Setup("Setup1", ops=[_Liar("Sticky")])
        _install(monkeypatch, [s])
        res = cd.handler(entity="Sticky")
        assert res["isError"] is True
        assert "still reads its name ('Sticky')" in res["message"]

    def test_an_ordinal_delete_takes_the_node_it_addressed_and_leaves_the_survivor(self,
                                                                                   monkeypatch):
        # Once 'DUP#1' goes the SURVIVOR is the only node named 'DUP', so re-resolving the address
        # after the delete reads IT and convicts a delete that took. The held node is the judge.
        first, second = Operation("DUP"), Operation("DUP")
        _install(monkeypatch, [Setup("S1", ops=[first]), Setup("S2", ops=[second])])
        out = _payload(cd.handler(entity="DUP#1"))
        assert out["deleted"] is True and out["entity_type"] == "operation"
        assert out["remaining_with_name"] == 1
        assert first.deleted is True and second.deleted is False

    def test_a_name_census_that_did_not_fall_is_an_error(self, monkeypatch):
        # the held node went quiet, but the tree still carries as many of that name - the second,
        # independent channel, which a delete that took cannot leave standing.
        s = Setup("Setup1")
        s.operations = _Regrown([Operation("Twin")])
        _install(monkeypatch, [s])
        res = cd.handler(entity="Twin")
        assert res["isError"] is True
        assert "1 CAM item(s) still carry the name 'Twin'" in res["message"]


class TestNCPrograms:
    """NC programs hang off cam.ncPrograms, outside the setup tree walk_cam_tree covers - cam_post's
    own rollback sentence points a failed one at cam_delete."""

    def test_a_program_is_deleted_by_name(self, monkeypatch):
        prog = _NCProgram("1003")
        cam = _install(monkeypatch, programs=[prog])
        out = _payload(cd.handler(entity="1003"))
        assert out["deleted"] is True and out["entity_type"] == "nc_program"
        assert prog.deleted is True and cam.ncPrograms.count == 0

    def test_a_program_and_an_operation_of_one_name_refuse_with_both_addresses(self, monkeypatch):
        s = Setup("Setup1", ops=[Operation("1003")])
        _install(monkeypatch, [s], programs=[_NCProgram("1003")])
        res = cd.handler(entity="1003")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "1003#1" in res["message"] and "1003#2" in res["message"]

    def test_a_program_whose_deleteme_declines_is_an_error(self, monkeypatch):
        cam = _install(monkeypatch, programs=[_NCProgram("1003", can_delete=False)])
        res = cd.handler(entity="1003")
        assert res["isError"] is True and "declin" in res["message"].lower()
        assert cam.ncPrograms.count == 1
