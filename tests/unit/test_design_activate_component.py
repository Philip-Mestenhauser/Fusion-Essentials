"""Unit tests for ``design_activate_component.py`` - the re-activate-an-existing-component primitive.

Pinned: an occurrence resolves by occurrence OR component name, an ambiguous name is REFUSED rather
than first-matched, an activate() that reports true but does not take is an error, and 'root'/''
returns the edit target to the root (falling back to deactivating the active occurrence).
"""

import json

import pytest

from conftest import load_tool

dm = load_tool("design_activate_component")


class _FakeOcc:
    def __init__(self, name, comp_name, activate_returns=True, full_path=None):
        self.name = name
        self.fullPathName = full_path or name
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

    def __iter__(self):
        return iter(self._occs)


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


def _install_activate(monkeypatch, design):
    app = type("A", (), {"activeProduct": design})()
    monkeypatch.setattr(dm._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, _ActivateDesign) else None)
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestActivateComponent:
    def test_no_active_design(self, monkeypatch):
        _install_activate(monkeypatch, None)
        # cast(None) -> None
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: None)
        res = dm.handler(occurrence="Chassis:1")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_activate_by_occurrence_name(self, monkeypatch):
        occ = _FakeOcc("Chassis:1", "Chassis")
        _install_activate(monkeypatch, _ActivateDesign([occ, _FakeOcc("Wheel:1", "Wheel")]))
        out = _payload(dm.handler(occurrence="Chassis:1"))
        assert occ.isActive is True
        assert out["activated"] == "Chassis:1" and out["component"] == "Chassis"
        assert out["active_component"] == "Chassis"

    def test_activate_by_component_name(self, monkeypatch):
        occ = _FakeOcc("Chassis:1", "Chassis")
        _install_activate(monkeypatch, _ActivateDesign([occ]))
        out = _payload(dm.handler(occurrence="Chassis"))   # component name
        assert occ.isActive is True and out["activated"] == "Chassis:1"

    def test_activation_that_does_not_take_bites(self, monkeypatch):
        # activate() returns true but the active component still reads root -> error, not ok
        occ = _FakeOcc("Chassis:1", "Chassis")
        occ.activate = lambda: True                    # true returned, isActive never flips
        _install_activate(monkeypatch, _ActivateDesign([occ]))
        res = dm.handler(occurrence="Chassis:1")
        assert res["isError"] is True
        assert "did not take" in res["message"]

    def test_unknown_component_errors_and_lists(self, monkeypatch):
        _install_activate(monkeypatch, _ActivateDesign([_FakeOcc("Wheel:1", "Wheel")]))
        res = dm.handler(occurrence="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"] and "Wheel:1" in res["message"]

    def test_ambiguous_name_refused_not_first_match(self, monkeypatch):
        # two instances share local name "Bolt:1" under different sub-assemblies - a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT silently activate the first.
        a = _FakeOcc("Bolt:1", "Bolt", full_path="Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Bolt", full_path="Sub-B:1+Bolt:1")
        _install_activate(monkeypatch, _ActivateDesign([a, b]))
        res = dm.handler(occurrence="Bolt")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        assert a.isActive is False and b.isActive is False

    def test_activate_root_via_empty(self, monkeypatch):
        occ = _FakeOcc("Chassis:1", "Chassis")
        occ.isActive = True
        called = {"root": False}
        def root_activate():
            called["root"] = True
            occ.isActive = False
            return True
        _install_activate(monkeypatch, _ActivateDesign([occ], root_activate=root_activate))
        out = _payload(dm.handler(occurrence=""))
        assert out["activated"] == "root"
        assert called["root"] is True

    def test_activate_root_falls_back_to_deactivate(self, monkeypatch):
        # no activateRootComponent on the design -> deactivate the active occurrence instead
        occ = _FakeOcc("Chassis:1", "Chassis")
        occ.isActive = True
        _install_activate(monkeypatch, _ActivateDesign([occ]))     # no root_activate provided
        out = _payload(dm.handler(occurrence="root"))
        assert out["activated"] == "root" and occ.deactivated is True

    def test_activate_returns_false_errors(self, monkeypatch):
        occ = _FakeOcc("Chassis:1", "Chassis", activate_returns=False)
        _install_activate(monkeypatch, _ActivateDesign([occ]))
        res = dm.handler(occurrence="Chassis:1")
        assert res["isError"] is True and "returned false" in res["message"]

    def test_deactivate_raises_surfaces_as_error(self, monkeypatch):
        # A deactivate() failure must propagate as an error - reporting ok("root") when the
        # deactivate call failed would be a false success.
        class _RaisingOcc(_FakeOcc):
            def deactivate(self):
                raise RuntimeError("deactivate blew up")
        occ = _RaisingOcc("Chassis:1", "Chassis")
        occ.isActive = True
        _install_activate(monkeypatch, _ActivateDesign([occ]))    # no root_activate -> falls to deactivate()
        with pytest.raises(RuntimeError, match="deactivate blew up"):
            dm.handler(occurrence="root")
