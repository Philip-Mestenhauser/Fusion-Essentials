"""Unit tests for ``design_activate_component.py`` - the re-activate-an-existing-component primitive.

Pinned: an occurrence resolves by occurrence OR component name, an ambiguous name is REFUSED rather
than first-matched, an activate() that reports true but does not take is an error, and 'root'/''
returns the edit target to the root through Design.activateRootComponent.
"""

from conftest import (FakeOccurrence, MakeComp, MakeDesign, error_message, install, load_tool,
                      payload)

dm = load_tool("design_activate_component")


class _Occ(FakeOccurrence):
    """An occurrence placing a component of its own."""

    def __init__(self, path, comp_name, **kw):
        super().__init__(path=path, component=MakeComp(name=comp_name), **kw)


class _ActivateDesign(MakeDesign):
    """A design whose activeComponent reports whichever occurrence is the active edit target - the
    read an activation is judged by. `root_activate` is Design.activateRootComponent."""

    def __init__(self, occurrences, root_activate=None):
        super().__init__(comp=MakeComp(name="RootComp", occurrences=occurrences))
        self._occs = list(occurrences)
        if root_activate is not None:
            self.activateRootComponent = root_activate

    @property
    def activeComponent(self):
        active = next((o for o in self._occs if o.isActive), None)
        return active.component if active is not None else self.rootComponent

    @activeComponent.setter
    def activeComponent(self, value):
        # MakeDesign seeds the root here; this design derives the answer from the active occurrence.
        pass


class TestActivateComponent:
    def test_no_active_design(self):
        install(dm, None)
        assert "No active design" in error_message(dm.handler(occurrence="Chassis:1"))

    def test_activate_by_occurrence_name(self):
        occ = _Occ("Chassis:1", "Chassis")
        install(dm, _ActivateDesign([occ, _Occ("Wheel:1", "Wheel")]))
        out = payload(dm.handler(occurrence="Chassis:1"))
        assert occ.isActive is True
        assert out["activated"] == "Chassis:1" and out["component"] == "Chassis"
        assert out["active_component"] == "Chassis"

    def test_activate_by_component_name(self):
        occ = _Occ("Chassis:1", "Chassis")
        install(dm, _ActivateDesign([occ]))
        out = payload(dm.handler(occurrence="Chassis"))   # component name
        assert occ.isActive is True and out["activated"] == "Chassis:1"

    def test_activation_that_does_not_take_bites(self):
        # activate() returns true but the active component still reads root -> error, not ok
        occ = _Occ("Chassis:1", "Chassis", activate_lies=True)
        install(dm, _ActivateDesign([occ]))
        assert "did not take" in error_message(dm.handler(occurrence="Chassis:1"))

    def test_unknown_component_errors_and_lists(self):
        install(dm, _ActivateDesign([_Occ("Wheel:1", "Wheel")]))
        msg = error_message(dm.handler(occurrence="Ghost"))
        assert "Ghost" in msg and "Wheel:1" in msg

    def test_ambiguous_name_refused_not_first_match(self):
        # two instances share local name "Bolt:1" under different sub-assemblies - a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT silently activate the first.
        a = _Occ("Sub-A:1+Bolt:1", "Bolt")
        b = _Occ("Sub-B:1+Bolt:1", "Bolt")
        install(dm, _ActivateDesign([a, b]))
        msg = error_message(dm.handler(occurrence="Bolt"))
        assert "ambiguous" in msg.lower()
        assert "Sub-A:1+Bolt:1" in msg and "Sub-B:1+Bolt:1" in msg
        assert a.isActive is False and b.isActive is False

    def test_activate_root_via_empty(self):
        occ = _Occ("Chassis:1", "Chassis", is_active=True)
        called = {"root": False}

        def root_activate():
            called["root"] = True
            occ._is_active = False
            return True

        install(dm, _ActivateDesign([occ], root_activate=root_activate))
        out = payload(dm.handler(occurrence=""))
        assert out["activated"] == "root"
        assert called["root"] is True

    def test_activate_returns_false_errors(self):
        occ = _Occ("Chassis:1", "Chassis", activate_ok=False)
        install(dm, _ActivateDesign([occ]))
        assert "returned false" in error_message(dm.handler(occurrence="Chassis:1"))
