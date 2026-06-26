"""Unit tests for ``component_tree.py`` occurrence search.

``_find_occurrence_by_name`` is a bounded depth-first search matching either a
substring of the occurrence name or an exact (lowercased) component name. The
bug surface: the substring-vs-exact distinction, case-insensitivity, and
descent into child occurrences.
"""

from types import SimpleNamespace

from conftest import load_tool

ctree = load_tool("component_tree")


def _occ(name, comp_name=None, children=()):
    return SimpleNamespace(
        name=name,
        component=SimpleNamespace(name=comp_name or name),
        childOccurrences=list(children),
    )


def _root(occurrences):
    return SimpleNamespace(occurrences=list(occurrences))


class TestFindOccurrenceByName:
    def test_substring_match_on_occurrence_name(self):
        root = _root([_occ("Vise_Body:1", comp_name="Vise")])
        found = ctree._find_occurrence_by_name(root, "vise_body")
        assert found is not None
        assert found.name == "Vise_Body:1"

    def test_exact_match_on_component_name(self):
        # component-name match is exact (not substring): "vise" == comp.name.lower()
        root = _root([_occ("inst:1", comp_name="Vise")])
        found = ctree._find_occurrence_by_name(root, "vise")
        assert found is not None
        assert found.component.name == "Vise"

    def test_descends_into_children(self):
        child = _occ("Jaw:1", comp_name="Jaw")
        parent = _occ("Vise:1", comp_name="Vise", children=[child])
        root = _root([parent])
        found = ctree._find_occurrence_by_name(root, "jaw")
        assert found is found  # sanity
        assert found.name == "Jaw:1"

    def test_no_match_returns_none(self):
        root = _root([_occ("Vise:1", comp_name="Vise")])
        assert ctree._find_occurrence_by_name(root, "nonexistent") is None

    def test_empty_tree_returns_none(self):
        assert ctree._find_occurrence_by_name(_root([]), "anything") is None
