"""Unit tests for ``model_set_material.py`` - assign a PHYSICAL material to bodies/component.

Pinned: the material search (exact name across document + libraries), the document-scope win, the
no-match candidate hint, the cross-library ambiguity refusal, per-body density read-back, and
partial-success reporting across a multi-body target.

Fakes are built locally with monkeypatch (conftest's shared fakes carry no material/library surface),
and BOTH design seams (the tool's own ``_common`` and ``_inputs._common``) are patched to the same
design - the dual-seam trap - so TargetRef('') resolves against the same root component the handler
reads.
"""

from types import SimpleNamespace

import pytest

from conftest import load_tool, payload as _payload

mm = load_tool("model_set_material")


class _Coll:
    """Fusion count/item(i) collection."""
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


def _material(name):
    return SimpleNamespace(name=name)


class _Lib:
    def __init__(self, name, materials):
        self.name = name
        self.materials = _Coll(materials)


class _Body:
    """A body whose ``.material`` setter records the assigned material (or raises when fail=True, to
    exercise partial success). ``physicalProperties.density`` is kg/cm3 (the API's unit)."""
    def __init__(self, name, density=None, fail=False):
        self.name = name
        self._mat = None
        self._fail = fail
        self.physicalProperties = SimpleNamespace(density=density)

    @property
    def material(self):
        return self._mat

    @material.setter
    def material(self, m):
        if self._fail:
            raise RuntimeError("body material is read-only in this context")
        self._mat = m


class _Design:
    def __init__(self, bodies, doc_materials=(), libraries=()):
        self.rootComponent = SimpleNamespace(name="Root", bRepBodies=_Coll(bodies))
        self.materials = _Coll(doc_materials)


@pytest.fixture
def wired(monkeypatch):
    """Build a fake design + material catalog and patch both design seams + the module ``app`` (whose
    ``materialLibraries`` the search reads). Returns the design."""
    def _make(bodies, doc_materials=(), libraries=()):
        design = _Design(bodies, doc_materials, libraries)
        monkeypatch.setattr(mm._common, "design", lambda: design)
        monkeypatch.setattr(mm._inputs._common, "design", lambda: design)
        monkeypatch.setattr(mm, "app", SimpleNamespace(materialLibraries=_Coll(libraries)))
        return design
    return _make


class TestHappyPath:
    def test_assigns_and_reads_back_density_in_kg_per_m3(self, wired):
        # steel 0.00785 kg/cm3 -> 7850 kg/m3
        wired([_Body("Body1", density=0.00785)],
              libraries=[_Lib("Fusion Material Library", [_material("Steel")])])
        out = _payload(mm.handler(target="", material="Steel"))
        assert out["assigned"] is True
        assert out["material"] == "Steel"
        assert out["source"] == "Fusion Material Library"
        assert out["density_kg_per_m3"] == 7850.0
        assert out["applied_to"][0]["body"] == "Body1"
        assert out["applied_to"][0]["density_kg_per_m3"] == 7850.0

    def test_case_insensitive_exact_match(self, wired):
        wired([_Body("B", density=0.0027)],
              libraries=[_Lib("Lib", [_material("Aluminum 6061")])])
        out = _payload(mm.handler(target="", material="aluminum 6061"))
        assert out["material"] == "Aluminum 6061"

    def test_declared_outputs_present(self, wired):
        wired([_Body("B", density=0.00785)], libraries=[_Lib("Lib", [_material("Steel")])])
        out = _payload(mm.handler(target="", material="Steel"))
        for r in mm.RETURNS:
            assert r.assert_present(out) == "", r.key


class TestSearchGuards:
    def test_no_match_lists_nearest_candidates(self, wired):
        wired([_Body("B")],
              libraries=[_Lib("Lib", [_material("Steel"), _material("Stainless Steel")])])
        res = mm.handler(target="", material="Steal")
        assert res["isError"] is True
        assert "No material named 'Steal'" in res["message"]
        assert "Steel" in res["message"]              # nearest offered, not silence

    def test_ambiguous_across_libraries_is_refused(self, wired):
        wired([_Body("B")],
              libraries=[_Lib("LibA", [_material("Brass")]), _Lib("LibB", [_material("Brass")])])
        res = mm.handler(target="", material="Brass")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "LibA" in res["message"] and "LibB" in res["message"]

    def test_document_material_wins_over_ambiguous_libraries(self, wired):
        # The same name in the document AND two libraries: the document copy is unambiguous and wins,
        # so this must NOT refuse.
        wired([_Body("B", density=0.0027)],
              doc_materials=[_material("Aluminum")],
              libraries=[_Lib("LibA", [_material("Aluminum")]), _Lib("LibB", [_material("Aluminum")])])
        out = _payload(mm.handler(target="", material="Aluminum"))
        assert out["source"] == "document"

    def test_empty_material_name_errors(self, wired):
        wired([_Body("B")], libraries=[_Lib("Lib", [_material("Steel")])])
        res = mm.handler(target="", material="")
        assert res["isError"] is True
        assert "material" in res["message"].lower()

    def test_no_catalog_reports_no_materials(self, wired):
        wired([_Body("B")], doc_materials=(), libraries=())
        res = mm.handler(target="", material="Steel")
        assert res["isError"] is True
        assert "No materials available" in res["message"]


class TestPartialSuccess:
    def test_partial_success_surfaced(self, wired):
        wired([_Body("Good", density=0.00785), _Body("Bad", fail=True)],
              libraries=[_Lib("Lib", [_material("Steel")])])
        out = _payload(mm.handler(target="", material="Steel"))
        assert out["assigned"] is True
        assert [a["body"] for a in out["applied_to"]] == ["Good"]
        assert out["failed"][0]["body"] == "Bad"
        assert "1 of 2" in out["note"]

    def test_all_bodies_fail_is_error(self, wired):
        wired([_Body("Bad", fail=True)], libraries=[_Lib("Lib", [_material("Steel")])])
        res = mm.handler(target="", material="Steel")
        assert res["isError"] is True
        assert "Could not assign" in res["message"]


class TestDesignGuard:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(mm._common, "design", lambda: None)
        res = mm.handler(target="", material="Steel")
        assert res["isError"] is True
        assert "design" in res["message"].lower()
