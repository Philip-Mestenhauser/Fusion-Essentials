"""Unit tests for the active-component targeting fix in sketch_core.py + model_extrude.py.

The bug the crane build exposed: both tools hardcoded design.rootComponent, so a
component made active via model_create_component(activate=true) never received the new
sketch/body — geometry leaked into root, leaving empty components. The fix routes
through a target_component(design) helper that returns design.activeComponent
(the current edit target) and falls back to rootComponent when none/unsupported,
so behaviour is unchanged when nothing is activated (activeComponent == root).
"""

from types import SimpleNamespace

from conftest import load_tool

sk = load_tool("sketch_core")
ex = load_tool("model_extrude")


def _design(active=None, root="ROOT"):
    d = SimpleNamespace(rootComponent=root)
    if active is not None:
        d.activeComponent = active
    return d


class TestSketchesTargetComponent:
    def test_uses_active_component_when_present(self):
        d = _design(active="MAST", root="ROOT")
        assert sk.target_component(d) == "MAST"

    def test_falls_back_to_root_when_no_active(self):
        # No activeComponent attribute at all -> root (back-compat).
        d = _design(active=None, root="ROOT")
        assert sk.target_component(d) == "ROOT"

    def test_falls_back_to_root_when_active_is_none(self):
        d = _design(active=None, root="ROOT")
        d.activeComponent = None
        assert sk.target_component(d) == "ROOT"


class TestExtrudeTargetComponent:
    def test_uses_active_component_when_present(self):
        d = _design(active="BOOM", root="ROOT")
        assert ex.target_component(d) == "BOOM"

    def test_falls_back_to_root(self):
        d = _design(active=None, root="ROOT")
        assert ex.target_component(d) == "ROOT"
