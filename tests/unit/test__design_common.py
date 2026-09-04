"""Unit tests for ``_design_common.py`` - the mode read and the base-feature scope runner.

Pinned (the definition of done):
  - get_mode_handler reports each designType + the capability `can{}` map (derived from the ONE true
    reader, so report and guards agree).
  - the wrapper finishes-in-a-FINALLY even when the inner op raises (a leaked open scope would
    corrupt later calls), and run_in_base_feature opens no scope at all in a direct design.
"""

import json

import pytest

from conftest import load_tool

dm = load_tool("_design_common")


# ── mode wiring (DesignTypes ints come seeded from live_api_facts) ──────────────────────────────

def _wire_modes(monkeypatch):
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion, "BaseFeature", _FakeBaseFeature)


class _FakeBaseFeature:
    """While a base feature is in edit, the API hides it from its owning collection (count drops,
    itemByName returns None) and the design reads direct - so the only handle to it is the object add()
    returned. startEdit/finishEdit toggle that visibility via the back-reference its collection sets on
    add()."""
    def __init__(self, name="BaseFeature1"):
        self.name = name
        self.editing = False
        self.start_returns = True
        self.finish_count = 0
        self.deleted = False
        self._coll = None        # set by _Coll.add() so edit can hide/show this item

    def startEdit(self):
        self.editing = True
        if self._coll is not None:
            self._coll._hide(self)        # the live API hides an in-edit base feature
        return self.start_returns

    def finishEdit(self):
        self.editing = False
        self.finish_count += 1
        if self._coll is not None:
            self._coll._show(self)        # re-enumerable once the edit closes
        return True

    def deleteMe(self):
        self.deleted = True
        if self._coll is not None:
            self._coll._remove(self)
        return True


class _Coll:
    """Counted collection with itemByName + add(). Mirrors the live API: an in-edit base feature is
    HIDDEN (not in count / itemByName) - see _FakeBaseFeature."""
    def __init__(self, items=()):
        self._items = list(items)
        self._hidden = []
        self.added = []
        self.add_returns = None  # if set, add() returns this instead of a fresh base feature
        for it in self._items:    # adopt pre-seeded items so their edit toggles visibility
            if isinstance(it, _FakeBaseFeature):
                it._coll = self

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def itemByName(self, n):
        for it in self._items:
            if getattr(it, "name", None) == n:
                return it
        return None

    def add(self):
        if self.add_returns is not None:
            bf = self.add_returns
            if isinstance(bf, _FakeBaseFeature):    # a real fake: wire it up; a bool/None models add() failing
                bf._coll = self
                self._items.append(bf)
                self.added.append(bf)
            return bf
        bf = _FakeBaseFeature(name=f"BaseFeature{len(self._items) + 1}")
        bf._coll = self
        self._items.append(bf)
        self.added.append(bf)
        return bf

    def _hide(self, bf):
        if bf in self._items:
            self._items.remove(bf)
            self._hidden.append(bf)

    def _show(self, bf):
        if bf in self._hidden:
            self._hidden.remove(bf)
            self._items.append(bf)

    def _remove(self, bf):
        if bf in self._items:
            self._items.remove(bf)
        if bf in self._hidden:
            self._hidden.remove(bf)


class _Features:
    def __init__(self, base_features):
        self.baseFeatures = base_features


class _Comp:
    def __init__(self, name="Comp", base_features=None):
        self.name = name
        self.features = _Features(base_features if base_features is not None else _Coll())


class _AllComponents:
    def __init__(self, comps):
        self._comps = comps

    @property
    def count(self):
        return len(self._comps)

    def item(self, i):
        return self._comps[i] if 0 <= i < len(self._comps) else None


class _Timeline:
    def __init__(self, count):
        self._count = count

    @property
    def count(self):
        return self._count


class FakeDesign:
    """A design exposing designType (numeric), an optional timeline, a root component with
    baseFeatures, and an activeEditObject for base-feature-scope detection."""
    def __init__(self, design_type=1, timeline_count=0, base_features=None,
                 edit_object=None, no_timeline=False):
        self.designType = design_type
        if not no_timeline:
            self.timeline = _Timeline(timeline_count)
        # else: no `timeline` attribute at all -> safe(lambda: design.timeline) returns None
        bf = base_features if base_features is not None else _Coll()
        self.rootComponent = _Comp("Root", base_features=bf)
        self.activeComponent = self.rootComponent
        self.allComponents = _AllComponents([self.rootComponent])
        self.activeEditObject = edit_object


def _install(monkeypatch, design):
    """Point the shared _common.design/target_component at `design`."""
    _wire_modes(monkeypatch)
    app = type("A", (), {"activeProduct": design})()
    monkeypatch.setattr(dm._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    monkeypatch.setattr(dm._inputs._common, "design", lambda: design)
    monkeypatch.setattr(dm._inputs._common, "target_component", lambda d: d.rootComponent)
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── get_mode_handler (mode slice) ─────────────────────────────────────────────────────────

class TestGetMode:
    def test_no_active_design(self, monkeypatch):
        _install(monkeypatch, None)
        res = dm.get_mode_handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_reports_parametric_and_capabilities(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1, timeline_count=4))
        out = _payload(dm.get_mode_handler())
        assert out["design_type"] == "parametric"
        assert out["has_timeline"] is True
        assert out["timeline_feature_count"] == 4
        can = out["can"]
        # parametric: coordinate datums OFF, offset plane / timeline / base-feature / ->direct ON
        assert can["construction_point_by_coordinate"] is False
        assert can["construction_axis_by_line"] is False
        assert can["construction_plane_by_offset"] is True
        assert can["timeline_ops"] is True
        assert can["base_feature_scope"] is True
        assert can["convert_to_direct"] is True
        assert can["convert_to_parametric"] is False

    def test_reports_direct_and_capabilities(self, monkeypatch):
        # direct design: no timeline attribute at all
        _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True))
        out = _payload(dm.get_mode_handler())
        assert out["design_type"] == "direct"
        assert out["has_timeline"] is False
        assert out["timeline_feature_count"] is None
        can = out["can"]
        # direct: coordinate datums ON; timeline / base-feature OFF; ->parametric ON
        assert can["construction_point_by_coordinate"] is True
        assert can["construction_axis_by_line"] is True
        assert can["construction_plane_by_offset"] is True
        assert can["timeline_ops"] is False
        assert can["base_feature_scope"] is False
        assert can["convert_to_direct"] is False
        assert can["convert_to_parametric"] is True

    def test_counts_base_features(self, monkeypatch):
        bf = _Coll([_FakeBaseFeature("BF1"), _FakeBaseFeature("BF2")])
        _install(monkeypatch, FakeDesign(design_type=1, timeline_count=2, base_features=bf))
        out = _payload(dm.get_mode_handler())
        assert out["base_feature_count"] == 2

    def test_in_base_feature_edit_true_when_editing(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1, edit_object=_FakeBaseFeature()))
        out = _payload(dm.get_mode_handler())
        assert out["in_base_feature_edit"] is True


# ── the leak-proof wrapper: finish-in-finally even when the inner op raises ──────────────────────

class TestBaseFeatureWrapper:
    def test_inner_op_runs_inside_scope_and_scope_finishes(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        bf = _FakeBaseFeature("W")
        seen = {}

        def open_scope():
            return bf, None

        def inner(b):
            seen["editing_during_op"] = b.editing
            return "result"

        out_bf, result = dm.base_feature_run_wrapper(open_scope, inner)
        assert out_bf is bf and result == "result"
        assert seen["editing_during_op"] is True      # the op saw an OPEN scope
        assert bf.finish_count == 1 and bf.editing is False  # and it was finished

    def test_scope_finishes_in_finally_when_inner_raises(self, monkeypatch):
        # A raising inner op must still finish the scope (a leaked open base-feature edit corrupts later
        # tool calls), and the error must propagate.
        _install(monkeypatch, FakeDesign(design_type=1))
        bf = _FakeBaseFeature("W")

        def open_scope():
            return bf, None

        def inner(b):
            raise RuntimeError("inner op exploded")

        with pytest.raises(RuntimeError, match="inner op exploded"):
            dm.base_feature_run_wrapper(open_scope, inner)
        assert bf.finish_count == 1 and bf.editing is False   # finished despite the raise

    def test_open_scope_error_short_circuits_before_any_scope(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        err = dm.error("cannot open")

        def open_scope():
            return None, err

        ran = {"inner": False}

        def inner(b):
            ran["inner"] = True

        out_bf, result = dm.base_feature_run_wrapper(open_scope, inner)
        assert out_bf is None and result is err and ran["inner"] is False

    def test_startEdit_false_in_wrapper_errors_without_running_inner(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        bf = _FakeBaseFeature("W")
        bf.start_returns = False
        ran = {"inner": False}

        def open_scope():
            return bf, None

        def inner(b):
            ran["inner"] = True

        out_bf, result = dm.base_feature_run_wrapper(open_scope, lambda b: inner(b))
        assert result["isError"] is True and "startEdit returned false" in result["message"]
        assert ran["inner"] is False


# ── run_in_base_feature: the BLESSED mode-aware helper mesh write tools import ────────────────────

class TestRunInBaseFeature:
    def test_direct_runs_inner_directly_with_no_scope(self, monkeypatch):
        # DIRECT design: inner_op runs directly, gets None, and NO base feature is add()ed.
        des = _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True))
        comp = des.rootComponent
        seen = {}

        def inner(bf):
            seen["bf"] = bf
            return "direct-result"

        result, err = dm.run_in_base_feature(des, comp, inner)
        assert err is None and result == "direct-result"
        assert seen["bf"] is None                                  # inner got None (no scope)
        assert comp.features.baseFeatures.added == []             # add() was NEVER called

    def test_parametric_runs_inner_inside_atomic_scope(self, monkeypatch):
        # PARAMETRIC: a fresh base feature is add()ed, opened, inner runs inside it, then it finishes.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        comp = des.rootComponent
        seen = {}

        def inner(bf):
            seen["editing_during_op"] = bf.editing
            return "param-result"

        result, err = dm.run_in_base_feature(des, comp, inner)
        assert err is None and result == "param-result"
        bf = comp.features.baseFeatures.added[-1]
        assert seen["editing_during_op"] is True                  # op saw an OPEN scope
        assert bf.finish_count == 1 and bf.editing is False       # and it was finished

    def test_parametric_finishes_in_finally_when_inner_raises(self, monkeypatch):
        # The helper must finish the scope even when the inner op raises, and propagate the error.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        comp = des.rootComponent

        def inner(bf):
            raise RuntimeError("mesh import exploded")

        with pytest.raises(RuntimeError, match="mesh import exploded"):
            dm.run_in_base_feature(des, comp, inner)
        bf = comp.features.baseFeatures.added[-1]
        assert bf.finish_count == 1 and bf.editing is False       # finished despite the raise

    def test_parametric_open_failure_returns_error_not_crash(self, monkeypatch):
        # add() returning nothing surfaces as a ready-to-return error, and inner never runs.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        comp = des.rootComponent
        comp.features.baseFeatures.add_returns = False             # add() yields a falsy value
        ran = {"inner": False}

        def inner(bf):
            ran["inner"] = True

        result, err = dm.run_in_base_feature(des, comp, inner)
        assert result is None and err is not None and err["isError"] is True
        assert ran["inner"] is False
