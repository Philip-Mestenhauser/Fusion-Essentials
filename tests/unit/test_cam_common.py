# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for the shared CAM finders in ``_cam_common`` - find_setup / find_operation /
walk_operations / setup_names. These are the ONE case-insensitive resolution every CAM tool shares, so
'Setup1' vs 'setup1' resolves the same everywhere. The fakes cover BOTH allOperations shapes the CAM
test suite uses (a Fusion count/item collection AND a plain list)."""

from types import SimpleNamespace

from conftest import load_tool

cc = load_tool("_cam_common")


class _Coll:
    """A Fusion-style count/item collection."""
    def __init__(self, items):
        self._i = list(items)

    @property
    def count(self):
        return len(self._i)

    def item(self, i):
        return self._i[i]


def _op(name):
    return SimpleNamespace(name=name)


def _setup(name, ops, as_list=False):
    return SimpleNamespace(name=name, allOperations=(list(ops) if as_list else _Coll(ops)))


def _cam(setups):
    return SimpleNamespace(setups=_Coll(setups))


class TestFindSetup:
    def test_found_case_insensitive(self):
        cam = _cam([_setup("Setup1", []), _setup("Setup2", [])])
        s, avail = cc.find_setup(cam, "setup2")          # lowercase input resolves 'Setup2'
        assert s is not None and s.name == "Setup2"
        assert avail == ["Setup1", "Setup2"]

    def test_not_found_returns_available(self):
        cam = _cam([_setup("Setup1", [])])
        s, avail = cc.find_setup(cam, "Ghost")
        assert s is None and avail == ["Setup1"]

    def test_empty_cam_is_safe(self):
        s, avail = cc.find_setup(_cam([]), "x")
        assert s is None and avail == []


class TestSetupNames:
    def test_lists_all_setup_names(self):
        assert cc.setup_names(_cam([_setup("A", []), _setup("B", [])])) == ["A", "B"]


class TestWalkOperations:
    def test_flattens_across_setups_countitem(self):
        cam = _cam([_setup("S1", [_op("Face1"), _op("Adaptive1")]),
                    _setup("S2", [_op("Drill1")])])
        assert [o.name for o in cc.walk_operations(cam)] == ["Face1", "Adaptive1", "Drill1"]

    def test_handles_list_backed_alloperations(self):
        # some CAM fakes back allOperations with a plain list, not count/item - both must iterate.
        cam = _cam([_setup("S1", [_op("Face1")], as_list=True)])
        assert [o.name for o in cc.walk_operations(cam)] == ["Face1"]


class TestFindOperation:
    def test_found_case_insensitive_across_setups(self):
        cam = _cam([_setup("S1", [_op("Face1")]), _setup("S2", [_op("Drill1")])])
        op, avail = cc.find_operation(cam, "drill1")     # lowercase input resolves 'Drill1'
        assert op is not None and op.name == "Drill1"
        assert avail == ["Face1", "Drill1"]

    def test_not_found_returns_available(self):
        cam = _cam([_setup("S1", [_op("Face1")])])
        op, avail = cc.find_operation(cam, "Ghost")
        assert op is None and avail == ["Face1"]
