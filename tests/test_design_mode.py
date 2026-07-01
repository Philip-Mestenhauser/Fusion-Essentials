"""Unit tests for design_mode.py — get_mode_handler (design_get's mode slice) / design_set_mode / model_base_feature.

Pinned (the definition of done):
  • get_mode_handler reports each designType + the capability `can{}` map (derived from the ONE true
    reader, so report and guards agree).
  • design_set_mode REFUSES parametric->direct without confirm, SUCCEEDS with confirm, and
    direct->parametric is free (no confirm); idempotent no-op when already in target.
  • model_base_feature opens then finishes a scope; the wrapper finishes-in-a-FINALLY even when the
    inner op raises (a leaked open scope would corrupt later calls).
  • mode-guard rejection names the required mode — base features need PARAMETRIC, and the message says
    so when refusing in direct.
"""

import json

import pytest

from conftest import load_tool

dm = load_tool("design_mode")


# ── mode wiring (the confirmed-live numeric convention: Parametric==1, Direct==0) ───────────────

def _wire_modes():
    import adsk.fusion
    dts = adsk.fusion.DesignTypes
    dts.ParametricDesignType = 1
    dts.DirectDesignType = 0
    adsk.fusion.BaseFeature = _FakeBaseFeature


class _FakeBaseFeature:
    """While a base feature is in edit, the API hides it from its owning collection (count drops,
    itemByName returns None) and the design reads direct — so the only handle to it is the object add()
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
    HIDDEN (not in count / itemByName) — see _FakeBaseFeature."""
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
    def __init__(self, name="Comp", base_features=None, timeline_node=False):
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
                 edit_object=None, no_timeline=False, raise_on_set=False):
        self.designType = design_type
        self._raise_on_set = raise_on_set
        if not no_timeline:
            self.timeline = _Timeline(timeline_count)
        # else: no `timeline` attribute at all -> safe(lambda: design.timeline) returns None
        bf = base_features if base_features is not None else _Coll()
        self.rootComponent = _Comp("Root", base_features=bf)
        self.activeComponent = self.rootComponent
        self.allComponents = _AllComponents([self.rootComponent])
        self.activeEditObject = edit_object

    def __setattr__(self, name, value):
        if name == "designType" and getattr(self, "_raise_on_set", False):
            raise RuntimeError("designType assignment blew up")
        super().__setattr__(name, value)


def _install(design):
    """Point the tool's app + the shared _common.design/target_component at `design`."""
    _wire_modes()
    dm.app = type("A", (), {"activeProduct": design})()
    dm._common.app = dm.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    dm._inputs._common.design = lambda: design
    dm._inputs._common.target_component = lambda d: d.rootComponent
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


@pytest.fixture(autouse=True)
def _reset_open_scopes():
    """The captured-open-scope list is module state; clear it between tests so cases don't leak
    open scopes into each other."""
    dm._OPEN_BASE_FEATURES.clear()
    yield
    dm._OPEN_BASE_FEATURES.clear()


# ── get_mode_handler (mode slice) ─────────────────────────────────────────────────────────

class TestGetMode:
    def test_no_active_design(self):
        _install(None)
        res = dm.get_mode_handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_reports_parametric_and_capabilities(self):
        _install(FakeDesign(design_type=1, timeline_count=4))
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

    def test_reports_direct_and_capabilities(self):
        # direct design: no timeline attribute at all
        _install(FakeDesign(design_type=0, no_timeline=True))
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

    def test_counts_base_features(self):
        bf = _Coll([_FakeBaseFeature("BF1"), _FakeBaseFeature("BF2")])
        _install(FakeDesign(design_type=1, timeline_count=2, base_features=bf))
        out = _payload(dm.get_mode_handler())
        assert out["base_feature_count"] == 2

    def test_in_base_feature_edit_true_when_editing(self):
        _install(FakeDesign(design_type=1, edit_object=_FakeBaseFeature()))
        out = _payload(dm.get_mode_handler())
        assert out["in_base_feature_edit"] is True

    def test_capability_map_matches_modeguard(self):
        # the non-drift guarantee: the report's can{} is derived from the SAME reader the guards use.
        des = _install(FakeDesign(design_type=1, timeline_count=1))
        out = _payload(dm.get_mode_handler())
        good, _ = dm._PARAMETRIC_GUARD.check(des)
        assert good is True and out["can"]["base_feature_scope"] is True


# ── design_set_mode ─────────────────────────────────────────────────────────

class TestSetMode:
    def test_no_active_design(self):
        _install(None)
        res = dm.set_mode_handler(target="direct", confirm_history_loss=True)
        assert res["isError"] is True and "No active design" in res["message"]

    def test_bad_target(self):
        _install(FakeDesign(design_type=1))
        res = dm.set_mode_handler(target="hologram")
        assert res["isError"] is True and "must be one of" in res["message"]

    def test_parametric_to_direct_refused_without_confirm(self):
        des = _install(FakeDesign(design_type=1))
        res = dm.set_mode_handler(target="direct")            # no confirm
        assert res["isError"] is True
        assert "confirm_history_loss=true" in res["message"]
        assert "DIRECT" in res["message"] and "irreversible" in res["message"].lower()
        # and it must NOT have mutated the design
        assert des.designType == 1

    def test_parametric_to_direct_succeeds_with_confirm(self):
        des = _install(FakeDesign(design_type=1))
        out = _payload(dm.set_mode_handler(target="direct", confirm_history_loss=True))
        assert out["converted"] is True
        assert out["from"] == "parametric" and out["to"] == "direct"
        assert out["history_discarded"] is True
        assert des.designType == 0          # actually flipped to DirectDesignType

    def test_direct_to_parametric_is_free(self):
        # the asymmetry: no confirm needed, no history discarded
        des = _install(FakeDesign(design_type=0, no_timeline=True))
        out = _payload(dm.set_mode_handler(target="parametric"))
        assert out["converted"] is True
        assert out["from"] == "direct" and out["to"] == "parametric"
        assert out["history_discarded"] is False
        assert des.designType == 1

    def test_idempotent_noop_when_already_target(self):
        des = _install(FakeDesign(design_type=1))
        out = _payload(dm.set_mode_handler(target="parametric"))
        assert out["converted"] is False and "Already" in out["note"]
        assert des.designType == 1

    def test_assignment_exception_surfaces_not_swallowed(self):
        # a real failure on the mutation is surfaced as an error (never safe()-swallowed to a false ok)
        des = _install(FakeDesign(design_type=0, no_timeline=True, raise_on_set=True))
        res = dm.set_mode_handler(target="parametric")
        assert res["isError"] is True and "Could not convert" in res["message"]


# ── model_base_feature ──────────────────────────────────────────────────────

class TestBaseFeature:
    def test_no_active_design(self):
        _install(None)
        res = dm.base_feature_handler(action="start")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_refused_in_direct_names_parametric(self):
        # refusing in a direct design, the error names PARAMETRIC as the requirement (not the inverse).
        _install(FakeDesign(design_type=0, no_timeline=True))
        res = dm.base_feature_handler(action="start")
        assert res["isError"] is True
        assert "needs parametric mode" in res["message"]
        assert "in direct mode" in res["message"]

    def test_bad_action(self):
        _install(FakeDesign(design_type=1))
        res = dm.base_feature_handler(action="dance")
        assert res["isError"] is True and "must be one of" in res["message"]

    def test_start_opens_a_scope(self):
        des = _install(FakeDesign(design_type=1))
        out = _payload(dm.base_feature_handler(action="start"))
        assert out["editing"] is True
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.editing is True                 # startEdit() actually called
        assert out["base_feature"] == bf.name
        # the open scope was CAPTURED (the only handle to it — it is now invisible to enumeration)
        assert out["open_scope_count"] == 1
        assert bf in dm._OPEN_BASE_FEATURES

    def test_start_names_the_base_feature(self):
        des = _install(FakeDesign(design_type=1))
        out = _payload(dm.base_feature_handler(action="start", base_feature="MeshScope"))
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.name == "MeshScope" and out["base_feature"] == "MeshScope"

    def test_start_errors_and_cleans_up_when_startEdit_returns_false(self):
        des = _install(FakeDesign(design_type=1))
        bf = _FakeBaseFeature()
        bf.start_returns = False
        des.rootComponent.features.baseFeatures.add_returns = bf
        res = dm.base_feature_handler(action="start")
        assert res["isError"] is True and "startEdit returned false" in res["message"]
        # the orphan feature is deleted and nothing is captured
        assert bf.deleted is True
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_closes_the_captured_open_scope(self):
        # THE REGRESSION TEST for the live wedge bug: start opens a scope (which the API then HIDES
        # from enumeration), finish must close THAT captured object — not try to re-find it by name.
        des = _install(FakeDesign(design_type=1))
        start = _payload(dm.base_feature_handler(action="start"))
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.editing is True
        # while open the API hides it: count drops, itemByName is None (the trap the old finish fell in)
        assert des.rootComponent.features.baseFeatures.count == 0
        assert des.rootComponent.features.baseFeatures.itemByName(bf.name) is None
        out = _payload(dm.base_feature_handler(action="finish"))
        assert bf.finish_count == 1 and bf.editing is False        # the captured scope was closed
        assert out["open_scope_count"] == 0
        assert len(out["closed_scopes"]) == 1
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_closes_multiple_captured_scopes(self):
        des = _install(FakeDesign(design_type=1))
        dm.base_feature_handler(action="start")
        dm.base_feature_handler(action="start")
        added = des.rootComponent.features.baseFeatures.added
        out = _payload(dm.base_feature_handler(action="finish"))
        assert len(out["closed_scopes"]) == 2
        assert all(bf.finish_count == 1 for bf in added)
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_named_also_closes_an_enumerable_feature(self):
        # a NOT-in-edit base feature named X (e.g. opened elsewhere and already closed) can still be
        # finished by name as a harmless no-op convenience.
        bf = _FakeBaseFeature("Scope1")
        _install(FakeDesign(design_type=1, base_features=_Coll([bf])))
        out = _payload(dm.base_feature_handler(action="finish", base_feature="Scope1"))
        assert out["editing"] is False and bf.finish_count == 1

    def test_finish_unknown_name_is_not_an_error(self):
        # finish must NEVER error on a missing name — erroring without closing was the original wedge.
        # An unknown name simply finds nothing to finish by name; it still closes captured scopes.
        _install(FakeDesign(design_type=1))
        out = _payload(dm.base_feature_handler(action="finish", base_feature="Ghost"))
        assert out["named_finished"] is None and out["open_scope_count"] == 0

    def test_finish_no_open_scope_is_idempotent(self):
        _install(FakeDesign(design_type=1))
        out = _payload(dm.base_feature_handler(action="finish"))
        assert out["editing"] is False and out["closed_scopes"] == []

    def test_finish_works_while_design_reads_direct(self):
        # while a scope is open the design READS direct; finish must NOT gate on mode. We simulate the
        # captured open scope on a design reading direct and confirm finish still closes it.
        des = _install(FakeDesign(design_type=0, no_timeline=True))
        bf = _FakeBaseFeature("Open")
        bf._coll = des.rootComponent.features.baseFeatures
        dm._OPEN_BASE_FEATURES.append(bf)
        out = _payload(dm.base_feature_handler(action="finish"))
        assert bf.finish_count == 1 and out["open_scope_count"] == 0


# ── the leak-proof wrapper: finish-in-finally even when the inner op raises ──────────────────────

class TestBaseFeatureWrapper:
    def test_inner_op_runs_inside_scope_and_scope_finishes(self):
        _install(FakeDesign(design_type=1))
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

    def test_scope_finishes_in_finally_when_inner_raises(self):
        # A raising inner op must still finish the scope (a leaked open base-feature edit corrupts later
        # tool calls), and the error must propagate.
        _install(FakeDesign(design_type=1))
        bf = _FakeBaseFeature("W")

        def open_scope():
            return bf, None

        def inner(b):
            raise RuntimeError("inner op exploded")

        with pytest.raises(RuntimeError, match="inner op exploded"):
            dm.base_feature_run_wrapper(open_scope, inner)
        assert bf.finish_count == 1 and bf.editing is False   # finished despite the raise

    def test_open_scope_error_short_circuits_before_any_scope(self):
        _install(FakeDesign(design_type=1))
        err = dm.error("cannot open")

        def open_scope():
            return None, err

        ran = {"inner": False}

        def inner(b):
            ran["inner"] = True

        out_bf, result = dm.base_feature_run_wrapper(open_scope, inner)
        assert out_bf is None and result is err and ran["inner"] is False

    def test_startEdit_false_in_wrapper_errors_without_running_inner(self):
        _install(FakeDesign(design_type=1))
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
    def test_direct_runs_inner_directly_with_no_scope(self):
        # DIRECT design: inner_op runs directly, gets None, and NO base feature is add()ed.
        des = _install(FakeDesign(design_type=0, no_timeline=True))
        comp = des.rootComponent
        seen = {}

        def inner(bf):
            seen["bf"] = bf
            return "direct-result"

        result, err = dm.run_in_base_feature(des, comp, inner)
        assert err is None and result == "direct-result"
        assert seen["bf"] is None                                  # inner got None (no scope)
        assert comp.features.baseFeatures.added == []             # add() was NEVER called

    def test_parametric_runs_inner_inside_atomic_scope(self):
        # PARAMETRIC: a fresh base feature is add()ed, opened, inner runs inside it, then it finishes.
        des = _install(FakeDesign(design_type=1))
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

    def test_parametric_finishes_in_finally_when_inner_raises(self):
        # The helper must finish the scope even when the inner op raises, and propagate the error.
        des = _install(FakeDesign(design_type=1))
        comp = des.rootComponent

        def inner(bf):
            raise RuntimeError("mesh import exploded")

        with pytest.raises(RuntimeError, match="mesh import exploded"):
            dm.run_in_base_feature(des, comp, inner)
        bf = comp.features.baseFeatures.added[-1]
        assert bf.finish_count == 1 and bf.editing is False       # finished despite the raise

    def test_parametric_open_failure_returns_error_not_crash(self):
        # add() returning nothing surfaces as a ready-to-return error, and inner never runs.
        des = _install(FakeDesign(design_type=1))
        comp = des.rootComponent
        comp.features.baseFeatures.add_returns = False             # add() yields a falsy value
        ran = {"inner": False}

        def inner(bf):
            ran["inner"] = True

        result, err = dm.run_in_base_feature(des, comp, inner)
        assert result is None and err is not None and err["isError"] is True
        assert ran["inner"] is False


# ── design_activate_component (the missing 're-activate an existing component' primitive) ────────

class _FakeOcc:
    def __init__(self, name, comp_name, activate_returns=True):
        self.name = name
        self.component = type("C", (), {"name": comp_name})()
        self.isActive = False
        self._activate_returns = activate_returns
        self.deactivated = False

    def activate(self):
        if self._activate_returns:
            self.isActive = True
        return self._activate_returns

    def deactivate(self):
        self.isActive = False
        self.deactivated = True
        return True


class _OccColl:
    def __init__(self, occs):
        self._occs = list(occs)

    @property
    def count(self):
        return len(self._occs)

    def item(self, i):
        return self._occs[i] if 0 <= i < len(self._occs) else None


class _ActivateDesign:
    """A design exposing root.allOccurrences + activeComponent, for design_activate_component."""
    def __init__(self, occs, root_activate=None):
        self._occs = occs
        active = type("Root", (), {"name": "RootComp", "allOccurrences": _OccColl(occs)})()
        self.rootComponent = active
        self._active_name = "RootComp"
        if root_activate is not None:
            self.activateRootComponent = root_activate

    @property
    def activeComponent(self):
        # report whichever occurrence is active, else root
        for o in self._occs:
            if o.isActive:
                return type("AC", (), {"name": o.component.name})()
        return type("AC", (), {"name": "RootComp"})()


def _install_activate(design):
    dm.app = type("A", (), {"activeProduct": design})()
    dm._common.app = dm.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _ActivateDesign) else None
    return design


class TestActivateComponent:
    def test_no_active_design(self):
        _install_activate(None)
        # cast(None) -> None
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = dm.activate_component_handler(occurrence="Chassis:1")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_activate_by_occurrence_name(self):
        occ = _FakeOcc("Chassis:1", "Chassis")
        _install_activate(_ActivateDesign([occ, _FakeOcc("Wheel:1", "Wheel")]))
        out = _payload(dm.activate_component_handler(occurrence="Chassis:1"))
        assert occ.isActive is True
        assert out["activated"] == "Chassis:1" and out["component"] == "Chassis"
        assert out["active_component"] == "Chassis"

    def test_activate_by_component_name(self):
        occ = _FakeOcc("Chassis:1", "Chassis")
        _install_activate(_ActivateDesign([occ]))
        out = _payload(dm.activate_component_handler(occurrence="Chassis"))   # component name
        assert occ.isActive is True and out["activated"] == "Chassis:1"

    def test_unknown_component_errors_and_lists(self):
        _install_activate(_ActivateDesign([_FakeOcc("Wheel:1", "Wheel")]))
        res = dm.activate_component_handler(occurrence="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"] and "Wheel:1" in res["message"]

    def test_activate_root_via_empty(self):
        occ = _FakeOcc("Chassis:1", "Chassis")
        occ.isActive = True
        called = {"root": False}
        def root_activate():
            called["root"] = True
            occ.isActive = False
            return True
        _install_activate(_ActivateDesign([occ], root_activate=root_activate))
        out = _payload(dm.activate_component_handler(occurrence=""))
        assert out["activated"] == "root"
        assert called["root"] is True

    def test_activate_root_falls_back_to_deactivate(self):
        # no activateRootComponent on the design → deactivate the active occurrence instead
        occ = _FakeOcc("Chassis:1", "Chassis")
        occ.isActive = True
        _install_activate(_ActivateDesign([occ]))     # no root_activate provided
        out = _payload(dm.activate_component_handler(occurrence="root"))
        assert out["activated"] == "root" and occ.deactivated is True

    def test_activate_returns_false_errors(self):
        occ = _FakeOcc("Chassis:1", "Chassis", activate_returns=False)
        _install_activate(_ActivateDesign([occ]))
        res = dm.activate_component_handler(occurrence="Chassis:1")
        assert res["isError"] is True and "returned false" in res["message"]
