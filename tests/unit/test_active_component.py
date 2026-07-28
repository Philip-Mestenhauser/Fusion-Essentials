"""Unit tests for active-component targeting in sketch_core.py + model_extrude.py.

A component made active via model_create_component(activate=true) must receive new
sketches/bodies, not the root component - hardcoding design.rootComponent would leak geometry
into root and leave the activated component empty. Both tools route through the shared
target_component(design) helper; one pin per module proves the seam is wired. The helper's own
behaviour (active wins, root fallback) is pinned in test_common.py.
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


class TestExtrudeTargetComponent:
    def test_uses_active_component_when_present(self):
        d = _design(active="BOOM", root="ROOT")
        assert ex.target_component(d) == "BOOM"
