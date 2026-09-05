"""Unit tests for ``assembly_rigid_group.py`` - collecting occurrences into RigidGroups.add.

The logic pinned here, no live Fusion: the at-least-two guard, the resolve of every named
occurrence through the shared ambiguity-refusing resolver, and the member-count read-back.
"""

import pytest

from conftest import (FakeOccurrence, FakeRigidGroup, FakeRigidGroups, MakeComp, MakeDesign,
                      install, load_tool, payload)


asm = load_tool("assembly_rigid_group")


def _occurrence(path):
    """One occurrence a group can take in, placing a component of its own."""
    return FakeOccurrence(path=path, component=MakeComp(name=path.split("+")[-1].split(":")[0]))


@pytest.fixture
def wire():
    """Build a design placing `paths` under a RigidGroups collection and wire both tool seams."""
    def build(*paths, new_group=None):
        occs = [_occurrence(p) for p in paths]
        root = MakeComp(occurrences=occs)
        root.rigidGroups = FakeRigidGroups(new_group=new_group)
        install(asm, MakeDesign(comp=root))
        return occs, root.rigidGroups
    return build


class TestRigidGroup:
    def test_group_reporting_fewer_members_bites(self, wire):
        # the group was created but reads fewer members than were requested -> error, not ok
        wire("A:1", "B:1", new_group=FakeRigidGroup(occurrences=["A:1"]))
        res = asm.handler(occurrences="A:1, B:1")
        assert res["isError"] is True
        assert "1 member(s)" in res["message"]

    def test_groups_named_occurrences(self, wire):
        _occs, groups = wire("A:1", "B:1", "C:1")
        out = payload(asm.handler(occurrences="A:1, B:1"))
        coll, _include = groups._added[-1]
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "B:1"]

    def test_include_children_flag(self, wire):
        _occs, groups = wire("A:1", "B:1")
        asm.handler(occurrences="A:1, B:1", include_children=True)
        _coll, include = groups._added[-1]
        assert include is True

    def test_needs_at_least_two(self, wire):
        wire("A:1")
        res = asm.handler(occurrences="A:1")
        assert res["isError"] is True and "at least two" in res["message"].lower()

    def test_missing_reported(self, wire):
        wire("A:1")
        res = asm.handler(occurrences="A:1, Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_accepts_a_list_not_just_comma_string(self, wire):
        # _resolve_many handles both a comma string and an actual list of names.
        _occs, groups = wire("A:1", "B:1", "C:1")
        out = payload(asm.handler(occurrences=["A:1", "C:1"]))
        coll, _include = groups._added[-1]
        assert coll.count == 2
        assert out["grouped"] == ["A:1", "C:1"]

    def test_list_with_blank_entries_filtered(self, wire):
        # empty/whitespace entries are dropped before resolution.
        wire("A:1", "B:1")
        out = payload(asm.handler(occurrences=["A:1", "  ", "B:1"]))
        assert out["grouped"] == ["A:1", "B:1"]
